import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect,QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
import longshot as ls

APP=QApplication.instance() or QApplication([])

class HorizontalTests(unittest.TestCase):
    def setUp(self):
        self.page=np.random.default_rng(307).integers(0,256,(240,1800,3),dtype=np.uint8)

    def test_new_columns_grow_width_without_rotating_or_changing_height(self):
        state=ls._HorizontalState(self.page[:,:400].copy())
        for x in (80,160,240):self.assertGreater(state.feed(self.page[:,x:x+400])[0],0)
        np.testing.assert_array_equal(state.long,self.page[:,:640])
        self.assertTrue(state.long.flags.c_contiguous)
        self.assertEqual(state.frames,4)

    def test_frozen_left_columns_and_right_panel_are_preserved_once(self):
        fixed=np.random.default_rng(41).integers(0,256,(240,200,3),dtype=np.uint8)
        def frame(x):return np.hstack((fixed[:,:60],self.page[:,x:x+260],fixed[:,60:140]))
        state=ls._HorizontalState(frame(0))
        for x in (40,80,120):state.feed(frame(x))
        np.testing.assert_array_equal(state.long,np.hstack((fixed[:,:60],self.page[:,:380],fixed[:,60:140])))

    def test_still_frames_and_backtracking_do_not_duplicate_columns(self):
        state=ls._HorizontalState(self.page[:,:400].copy())
        for x in (80,160,160,80,160,240,240):state.feed(self.page[:,x:x+400])
        np.testing.assert_array_equal(state.long,self.page[:,:640])

    def test_unrelated_frame_does_not_corrupt_accumulated_image(self):
        state=ls._HorizontalState(self.page[:,:400].copy())
        state.feed(self.page[:,80:480])
        before=state.long.copy()
        other=np.random.default_rng(99).integers(0,256,(240,400,3),dtype=np.uint8)
        self.assertEqual(state.feed(other)[0],-1)
        np.testing.assert_array_equal(state.long,before)

    def test_identical_grid_columns_do_not_invent_a_large_scroll(self):
        page=np.full((240,1600,3),245,np.uint8)
        page[:,::40]=80;page[::30,:]=120
        state=ls._HorizontalState(page[:,:400].copy())
        self.assertLessEqual(state.feed(page[:,13:413])[0],0)
        np.testing.assert_array_equal(state.long,page[:,:400])

class HorizontalToolbarTests(unittest.TestCase):
    def setUp(self):
        self.page=np.random.default_rng(23).integers(0,256,(240,1400,3),dtype=np.uint8)
        self.source_patch=patch('longshot.grab_array',return_value=self.page[:,:400].copy())
        self.source=self.source_patch.start()
        self.guards=[patch('longshot.QCursor.setPos'),patch('longshot.C.force_foreground'),patch('longshot.C.exclude_from_capture')]
        for item in self.guards:item.start()
        self.delivered=[]
        self.bar=ls.LongShotBar(lambda *a,**k:None,lambda:None,QRect(0,0,400,240),APP.primaryScreen(),lambda image,count:self.delivered.append(image.copy()))
        self.bar.timer.stop()

    def tearDown(self):
        self.bar.cancel();self.bar.deleteLater();APP.sendPostedEvents(None,QEvent.DeferredDelete)
        for item in reversed(self.guards):item.stop()
        self.source_patch.stop()

    def test_direction_control_is_discoverable_and_horizontal_has_no_dual_actions(self):
        self.assertIn('纵向',self.bar.baxis.text())
        self.bar.bcols.click()
        self.bar.set_horizontal(True)
        self.assertTrue(self.bar._horizontal)
        self.assertFalse(self.bar._dual)
        self.assertTrue(self.bar.bcols.isHidden())
        self.assertTrue(self.bar.blink.isHidden())
        self.assertTrue(self.bar.bfill.isHidden())
        self.assertIsNone(self.bar._divider)
        self.assertIsNone(self.bar._scroll_link._configuration)
        self.bar.set_horizontal(False)
        self.assertFalse(self.bar.bcols.isHidden())

    def test_switching_to_horizontal_clears_an_active_link_session(self):
        from longshot_link import LinkGeometry
        with patch('longshot.physical_geometry',return_value=LinkGeometry(0,0,400,240,200)):
            self.bar.bcols.click();self.bar.timer.start();self.bar.blink.click()
            self.assertIsNotNone(self.bar._scroll_link._configuration)
            self.bar.set_horizontal(True)
            self.assertIsNone(self.bar._scroll_link._configuration)
            self.assertFalse(self.bar.blink.isChecked())
            self.assertIsNone(self.bar._divider)

    def test_toolbar_reposition_reflows_preview_and_vertical_geometry_restores(self):
        original=QRect(self.bar._preview.geometry())
        self.bar.set_horizontal(True)
        def place(bar,*args,**kwargs):bar.move(15,50);return True
        with patch('longshot.C.place_toolbar_near_region',side_effect=place):
            self.bar._place_toolbar()
        self.assertFalse(self.bar._preview.geometry().intersects(self.bar.geometry().adjusted(-8,-8,8,8)))
        self.assertTrue(self.bar._preview._work_area.contains(self.bar._preview.geometry()))
        self.assertLessEqual(self.bar._preview._pm.height(),self.bar._preview.height()-12)
        self.bar.set_horizontal(False)
        self.assertEqual(self.bar._preview.geometry(),original)

    def test_horizontal_image_reaches_editor_and_direction_locks_after_append(self):
        self.bar.baxis.click()
        for x in (80,160,240):
            self.source.return_value=self.page[:,x:x+400].copy();self.bar.grab()
        self.assertFalse(self.bar.baxis.isEnabled())
        self.assertFalse(self.bar.set_horizontal(False))
        self.assertTrue(self.bar._horizontal)
        self.assertIn('640×240',self.bar.stat.text())
        self.assertGreater(self.bar._preview.width(),self.bar._preview.height())
        self.bar.edit()
        np.testing.assert_array_equal(self.delivered[0],self.page[:,:640])
        self.assertIsNone(self.bar.long_arr)
        self.assertIsNone(self.bar._horizontal_state)

    def test_copy_failure_preserves_horizontal_image_for_retry_without_saving(self):
        self.bar.set_horizontal(True)
        self.source.return_value=self.page[:,80:480].copy();self.bar.grab()
        with patch.object(self.bar,'_on_finish',side_effect=[False,True]) as copy,patch('longshot.C.save_image') as save:
            self.bar.finish(copy=True)
            self.assertIsNotNone(self.bar.long_arr)
            self.assertFalse(self.bar.baxis.isEnabled())
            self.bar.finish(copy=True)
            self.assertEqual(copy.call_count,2)
            image=copy.call_args.kwargs['image']
            self.assertEqual((image.width(),image.height()),(480,240))
            save.assert_not_called()

    def test_uncertain_horizontal_overlap_never_claims_completion_or_overwrites_clipboard(self):
        self.bar.set_horizontal(True)
        self.source.return_value=np.random.default_rng(92).integers(0,256,(240,400,3),dtype=np.uint8)
        with patch.object(self.bar,'_on_finish') as finish:
            for _ in range(self.bar.MISS_LIMIT+1):self.bar.grab()
            finish.assert_not_called()
            self.assertTrue(self.bar.running)
            self.assertIsNotNone(self.bar.long_arr)

if __name__=='__main__':unittest.main()
