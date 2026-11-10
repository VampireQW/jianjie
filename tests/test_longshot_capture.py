import os,sys,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage,QPixmap
from PySide6.QtWidgets import QApplication
import longshot as ls
APP=QApplication.instance() or QApplication([])

class Screen:
    def __init__(self,dpr):
        self.dpr=dpr;self.calls=[]
        self.array=np.random.default_rng(310).integers(0,256,(int(400*dpr),int(600*dpr),3),np.uint8)
        h,w=self.array.shape[:2]
        self.pixmap=QPixmap.fromImage(QImage(self.array.data,w,h,self.array.strides[0],QImage.Format_RGB888).copy())
    def geometry(self):return QRect(-313,-2160,600,400)
    def devicePixelRatio(self):return self.dpr
    def grabWindow(self,window,*args):
        self.calls.append(args)
        if not args:return self.pixmap.copy()
        x,y,w,h=args;d=self.dpr
        return self.pixmap.copy(QRect(round(x*d),round(y*d),round(w*d),round(h*d)))

class CaptureTests(unittest.TestCase):
    def test_region_is_captured_directly_without_a_full_screen_read(self):
        for d in (1.,1.25,1.5,1.75,2.):
            with self.subTest(dpr=d):
                screen=Screen(d);r=QRect(101,53,303,207)
                actual=ls.grab_array(screen,r)
                expected=screen.array[int(r.y()*d):int((r.y()+r.height())*d),int(r.x()*d):int((r.x()+r.width())*d)]
                np.testing.assert_array_equal(actual,expected)
                self.assertTrue(all(len(call)==4 for call in screen.calls))
                self.assertLess(screen.calls[0][2],screen.geometry().width())

    def test_partly_outside_region_clips_without_changing_pixel_origin(self):
        screen=Screen(1.5);actual=ls.grab_array(screen,QRect(-3,-5,100,100))
        np.testing.assert_array_equal(actual,screen.array[:142,:145])

if __name__=='__main__':unittest.main()
