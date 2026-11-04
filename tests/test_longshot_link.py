import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QEvent, QRect
from PySide6.QtWidgets import QApplication, QPushButton
from longshot import LongShotBar, _ColumnState
from longshot_link import LinkGeometry, WheelForwarder, scaled_geometry, wheel_parameters

APP = QApplication.instance() or QApplication([])


class LinkedToolbarTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch('longshot.grab_array', return_value=np.full((240,400,3),250,np.uint8)),
                        patch('longshot.QCursor.setPos'), patch('longshot.C.force_foreground'),
                        patch('longshot.C.exclude_from_capture'),
                        patch('longshot.physical_geometry', return_value=LinkGeometry(0,0,400,240,200))]
        for item in self.patches:item.start()
        self.bar = LongShotBar(lambda *args, **kwargs:None, lambda:None, QRect(0,0,400,240), APP.primaryScreen())
        self.bar.timer.stop()

    def tearDown(self):
        self.bar.cancel()
        self.bar.deleteLater()
        APP.sendPostedEvents(None,QEvent.DeferredDelete)
        for item in reversed(self.patches):item.stop()

    def test_linked_scrolling_is_opt_in_and_available_only_in_dual_mode(self):
        bar = self.bar
        button = bar.findChild(QPushButton, 'linkedScroll')
        self.assertIsNotNone(button, 'Dual long screenshots need an optional linked-scroll control')
        self.assertFalse(button.isChecked())
        self.assertTrue(button.isHidden())
        bar.bcols.click()
        self.assertFalse(button.isHidden())

    def linked(self):
        self.bar.bcols.click()
        self.bar.timer.start()
        self.bar.blink.click()
        self.assertTrue(self.bar.blink.isChecked())
        self.assertIsNotNone(self.bar._scroll_link._configuration)

    def test_off_and_on_keep_both_original_column_states(self):
        self.linked()
        left, right = self.bar._colL, self.bar._colR
        self.bar.blink.click()
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.bar.blink.click()
        self.assertIsNotNone(self.bar._scroll_link._configuration)
        self.assertIs(self.bar._colL,left)
        self.assertIs(self.bar._colR,right)

    def test_palette_and_picker_suspend_link_and_restore_only_when_active(self):
        self.linked()
        self.bar.open_fill_colors()
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.bar._fill_popup.hide()
        self.assertIsNotNone(self.bar._scroll_link._configuration)
        self.bar._fill_picking = True
        self.bar._sync_scroll_link()
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.bar._fill_picking = False
        self.bar.blink.click()
        self.bar._sync_scroll_link()
        self.assertIsNone(self.bar._scroll_link._configuration)

    def test_single_mode_clears_link_and_next_dual_starts_independent(self):
        self.linked()
        self.bar.bcols.click()
        self.assertTrue(self.bar.blink.isHidden())
        self.assertFalse(self.bar.blink.isChecked())
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.bar.bcols.click()
        self.assertFalse(self.bar.blink.isChecked())

    def test_cancel_and_failed_copy_stop_forwarding(self):
        self.linked()
        self.bar._on_finish = lambda *args, **kwargs:False
        self.bar.finish(copy=True)
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.assertFalse(self.bar.running)
        self.bar.cancel()
        self.assertTrue(self.bar._scroll_link._closed)

    def test_hide_and_native_failure_stop_forwarding(self):
        self.linked()
        self.bar.hide()
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.bar.show()
        self.bar._link_failed('Another window cannot receive input')
        self.assertFalse(self.bar.blink.isChecked())
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.assertIn('cannot receive', self.bar.msg.text())

    def test_divider_reconfiguration_uses_latest_fraction(self):
        self.linked()
        with patch('longshot.physical_geometry', side_effect=lambda screen, region, fraction:
                   LinkGeometry(0,0,400,240,round(400*fraction))):
            self.assertTrue(self.bar.commit_divider(.25))
        self.assertEqual(self.bar._scroll_link._configuration.split,100)


class FakeWindows:
    def __init__(self):
        self.posts=[]
        self.covered=False
        self.blocked=False
    def window_at(self,point):
        if self.covered and point[0]>=200:return 30
        return 10 if point[0]<200 else 20
    def root(self,handle):return handle*10
    def process(self,handle):return 999 if self.blocked else handle
    def post_wheel(self,handle,delta,point):
        self.posts.append((handle,delta,point))
        return True


class WheelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.api=FakeWindows()
        self.forwarder=WheelForwarder(self.api,LinkGeometry(0,0,400,240,200),999)

    def test_each_side_goes_only_to_peer_preserving_direction_and_small_delta(self):
        for x,delta in ((50,-120),(350,-15),(50,60)):
            self.assertTrue(self.forwarder.forward(x,100,delta))
        self.assertEqual(self.api.posts,[(20,-120,(300,100)),(10,-15,(100,100)),(20,60,(300,100))])

    def test_outside_divider_and_modified_wheels_never_forward(self):
        for x,y,modified in ((-1,10,False),(400,10,False),(50,240,False),(200,10,False),
                              (209,10,False),(50,10,True)):
            self.assertFalse(self.forwarder.forward(x,y,-120,modified))
        self.assertEqual(self.api.posts,[])

    def test_covered_or_own_app_window_is_not_scrolled(self):
        self.api.covered=True
        self.assertFalse(self.forwarder.forward(50,100,-120))
        self.api.covered=False
        self.api.blocked=True
        self.assertFalse(self.forwarder.forward(50,100,-120))
        self.assertEqual(self.api.posts,[])

    def test_unavailable_original_windows_cannot_start_session(self):
        self.api.blocked=True
        with self.assertRaises(OSError):
            WheelForwarder(self.api,LinkGeometry(0,0,400,240,200),999)

    def test_permission_failure_is_reported_instead_of_silently_claiming_success(self):
        self.api.post_wheel=lambda *args:False
        with self.assertRaises(OSError):self.forwarder.forward(50,100,-120)

    def test_negative_monitor_coordinates_and_high_dpi_use_monitor_native_origin(self):
        geometry=scaled_geometry((-2560,0,0,1440),(100,80,1000,700),1.5,.3)
        self.assertEqual(geometry,LinkGeometry(-2410,120,-910,1170,-1960,15))
        self.assertEqual(geometry.peer(-2200,600),(-1435,600))
        self.assertEqual(geometry.peer(-1200,600),(-2185,600))

    def test_screen_bounds_are_clipped_and_tiny_regions_rejected(self):
        self.assertEqual(scaled_geometry((1920,-100,3840,980),(0,0,960,540),2,.5),
                         LinkGeometry(1920,-100,3840,980,2880,20))
        with self.assertRaises(ValueError):scaled_geometry((0,0,1920,1080),(0,0,1,5),1,.5)

    def test_win32_signed_parameters_keep_negative_coordinates_and_wheel_delta(self):
        import ctypes
        parameter,point=wheel_parameters(-15,(-2000,-30))
        self.assertEqual(ctypes.c_short(parameter>>16).value,-15)
        self.assertEqual(ctypes.c_short(point&0xffff).value,-2000)
        self.assertEqual(ctypes.c_short(point>>16).value,-30)

    def test_both_columns_stitch_and_longer_column_continues_after_short_one_stops(self):
        rng=np.random.default_rng(304)
        short=rng.integers(0,256,(420,200,3),dtype=np.uint8)
        long=rng.integers(0,256,(660,200,3),dtype=np.uint8)
        left,right=_ColumnState(short[:240].copy()),_ColumnState(long[:240].copy())
        for offset in range(30,421,30):
            left.feed(short[min(180,offset):min(180,offset)+240])
            right.feed(long[offset:offset+240])
        np.testing.assert_array_equal(left.long,short)
        np.testing.assert_array_equal(right.long,long)


if __name__ == '__main__': unittest.main(verbosity=2)
