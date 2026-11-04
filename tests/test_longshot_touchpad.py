import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import ctypes as ct
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import longshot_link as link


class TouchpadTests(unittest.TestCase):
    def test_system_wheel_stream_without_raw_reports_reaches_peer_once(self):
        class API:
            def __init__(self):self.posts=[]
            def window_at(self,point):return 10 if point[0]<200 else 20
            def root(self,handle):return handle
            def process(self,handle):return handle
            def post_wheel(self,handle,delta,point):self.posts.append((handle,delta,point));return True
        api=API()
        forwarder=link.WheelForwarder(api,link.LinkGeometry(0,0,400,240,200),999)
        buffer=link.WheelEventBuffer()
        for delta in (-1,-1,-4,-12,3,1):
            buffer.add(1,(50,100,delta,False))
        for x,y,delta,modified in buffer.take(1):forwarder.forward(x,y,delta,modified)
        self.assertEqual(api.posts,[(20,d,(300,100)) for d in (-1,-1,-4,-12,3,1)])
        self.assertEqual(list(buffer.take(1)),[])

    def test_small_signed_wheel_deltas_and_inertia_are_not_rounded_to_notches(self):
        event=link.SystemMouseEvent()
        event.point.x,event.point.y=-1500,640
        for delta in (-1,-2,-40,-120,1,3,15,120):
            event.data=(delta&0xffff)<<16
            self.assertEqual(link.decode_system_wheel(0,0x20a,event),(-1500,640,delta))

    def test_horizontal_movement_and_negative_hook_codes_do_not_read_wheel_memory(self):
        for code,message in ((-1,0x20a),(0,0x200),(0,0x20e)):
            self.assertIsNone(link.decode_system_wheel(code,message,None))
        event=link.SystemMouseEvent()
        self.assertIsNone(link.decode_system_wheel(0,0x20a,event))

    def test_new_gesture_session_discards_pending_previous_events(self):
        buffer=link.WheelEventBuffer()
        buffer.add(10,(10,20,-1,False))
        buffer.add(11,(350,20,-2,False))
        self.assertEqual(list(buffer.take(11)),[(350,20,-2,False)])
        buffer.add(12,(10,20,15,False))
        buffer.clear()
        self.assertEqual(list(buffer.take(12)),[])

    def test_full_buffer_pauses_explicitly_instead_of_silent_drops(self):
        buffer=link.WheelEventBuffer()
        for index in range(256):buffer.add(1,(50,100,-1,False))
        with self.assertRaises(OverflowError):buffer.add(1,(50,100,-1,False))
        self.assertEqual(len(list(buffer.take(1))),256)

    def test_single_wakeup_drains_full_ordered_stream_and_next_gesture_wakes_again(self):
        buffer=link.WheelEventBuffer()
        self.assertTrue(buffer.add(1,(50,100,-1,False)))
        self.assertFalse(buffer.add(1,(50,100,1,False)))
        self.assertEqual(list(buffer.take(1)),[(50,100,-1,False),(50,100,1,False)])
        self.assertTrue(buffer.add(1,(50,100,-2,False)))


if __name__=='__main__':unittest.main(verbosity=2)
