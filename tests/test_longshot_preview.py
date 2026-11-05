"""Keep horizontal progress separate from the interactive recording toolbar."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QRect
import longshot as ls

class PreviewPlacementTests(unittest.TestCase):
    def place(self,selection,bounds,toolbar):
        result=ls.horizontal_preview_geometry(selection,bounds,toolbar)
        self.assertTrue(bounds.contains(result))
        self.assertFalse(result.intersects(toolbar.adjusted(-8,-8,8,8)))
        return result

    def test_normal_preview_is_above_selection_not_on_toolbar_below(self):
        selection=QRect(180,240,1100,500)
        result=self.place(selection,QRect(0,0,1600,1000),QRect(650,750,630,46))
        self.assertLess(result.bottom(),selection.top()-8)

    def test_preview_stacks_above_a_toolbar_that_flipped_to_top(self):
        selection=QRect(100,400,1300,560)
        toolbar=QRect(750,344,650,46)
        result=self.place(selection,QRect(0,0,1600,1000),toolbar)
        self.assertLess(result.bottom(),toolbar.top()-8)

    def test_top_edge_fallback_does_not_cover_toolbar_or_taskbar(self):
        selection=QRect(0,8,1440,700)
        result=self.place(selection,QRect(0,0,1440,840),QRect(730,718,700,46))
        self.assertGreaterEqual(result.height(),60)

    def test_full_screen_selection_has_room_for_preview_and_toolbar(self):
        result=self.place(QRect(0,0,1366,768),QRect(0,0,1366,720),QRect(620,668,720,46))
        self.assertLess(result.bottom(),668)

    def test_negative_display_origin_is_not_scaled_or_clamped_to_primary(self):
        selection=QRect(-2400,260,1250,530)
        result=self.place(selection,QRect(-2560,0,1920,1020),QRect(-1800,800,650,46))
        self.assertLess(result.right(),0)
        self.assertLess(result.bottom(),selection.top())

    def test_changed_toolbar_geometry_is_avoided_in_either_vertical_position(self):
        selection=QRect(100,200,1100,580);bounds=QRect(0,0,1440,900)
        for toolbar in (QRect(650,790,550,46),QRect(450,144,750,46)):
            self.place(selection,bounds,toolbar)

    def test_no_space_hides_preview_instead_of_covering_only_controls(self):
        self.assertTrue(ls.horizontal_preview_geometry(QRect(0,0,200,80),QRect(0,0,200,80),QRect(0,0,200,80)).isEmpty())

if __name__=='__main__':unittest.main()
