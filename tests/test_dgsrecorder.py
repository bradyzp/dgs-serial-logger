#!/usr/bin/python3.5

import unittest
from unittest.mock import patch
from unittest import mock
import SerialGrav
import threading
import serial
import time

"""
Skip a testcase with @unittest.skip('reason')
Assert exceptions using 'with' context:
    with self.assertRaises(Exception):
        do something that raises
"""

class test_Recorder(unittest.TestCase):
    def setUp(self):
        self.recorder = SerialGrav.Recorder() 

    def test_instance(self):
        self.assertIsInstance(self.recorder, SerialGrav.Recorder)

    @patch('serial.tools.list_ports.comports')
    def test_get_ports(self, mock_comports):
        #Mock up a ListPortInfo object to provide 'ttyS0' when comports() is called
        mock_port = mock.Mock()
        mock_port.name = 'ttyS0'
        mock_port.description = 'ttyS0'
        mock_port.device = '/dev/ttyS0'
        mock_comports.return_value = [mock_port]
        
        ports = self.recorder._get_ports()
        self.assertEqual(ports, ['ttyS0'])
        mock_comports.assert_called_once_with()

    def test_make_thread(self):
       thread = self.recorder._make_thread('ttyS0')
       self.assertIsInstance(thread, SerialGrav.SerialRecorder)
       self.assertEqual(thread.device, '/dev/ttyS0')
       #TODO: the name may change depending on order this test is executed
       self.assertEqual(thread.name, 'ttyS0')
       self.assertFalse(thread.is_alive())
    
    @patch('serial.Serial', return_value = serial.serial_for_url('loop://', timeout=0))
    @patch('SerialGrav.Recorder._get_ports', return_value=['ttyS0', 'ttyS1'])
    def test_spawn_threads(self, mock_ports, mock_serial):
       self.assertEqual(self.recorder._get_ports(), ['ttyS0', 'ttyS1'])
       self.assertEqual(self.recorder.threads, [])

       self.recorder.spawn_threads()
       self.assertEqual(len(self.recorder.threads), 2)
       self.assertEqual(self.recorder.threads[0].device, '/dev/ttyS0')
       self.assertEqual(self.recorder.threads[1].device, '/dev/ttyS1')

       mock_ports.return_value = ['ttyS3']
       self.recorder.spawn_threads()
       self.assertEqual(len(self.recorder.threads), 3)
       self.recorder.exiting.set()

    @patch('serial.Serial', return_value = serial.serial_for_url('loop://', timeout=0))
    def test_serial_read(self, mock_serial):
        s = mock_serial.return_value
        thread = self.recorder._make_thread('ttyS0')
        thread.start()
        s.write(b'test')
        #Sleep to ensure loop port has time to setup/send (may vary on diff system)
        time.sleep(.1)
        self.assertEqual(thread.data[0], 'test')
    
    
    @patch('SerialGrav.SerialRecorder.read_data', 
                side_effect=serial.SerialException("Error Reading"))
    def test_serial_read_exception(self, mock_read):
        """Test exception handling on bad readline()
        Expect an exception to be added to thread.exc
        and the thread should be killed"""

        thread = SerialGrav.SerialRecorder('ttyS0', threading.Event())
        thread.start()
        #sleep required to allow thread to start before assertions
        time.sleep(.1)
        self.assertTrue(mock_read.called)
        self.assertFalse(thread.exc is None)
        self.assertFalse(thread.is_alive())

    @patch('serial.Serial', return_value = serial.serial_for_url('loop://', timeout=0))
    @patch('SerialGrav.Recorder._get_ports', return_value=['ttyS0'])
    def test_serial_exception_recovery(self, mock_ports, mock_serial):
        """Test Recorder class to ensure thread is respawned
        after an exception is triggered.
        e.g. thread(/dev/ttyS0) -> exception -> returns
        Recorder should then spawn new thread(/dev/ttyS0)
        """
        self.recorder.spawn_threads()
        time.sleep(.1)
        self.assertEqual(len(self.recorder.threads), 1)
        self.assertEqual(self.recorder.threads[0].device, '/dev/ttyS0')
        with patch('SerialGrav.SerialRecorder.read_data', 
                side_effect=serial.SerialException("mock error")) as mock_exc:
            time.sleep(.1)
            self.assertNotEqual(self.recorder.threads[0].exc, None)
        self.assertFalse(self.recorder.threads[0].is_alive())
        self.recorder.scrub_threads()
        self.assertEqual(len(self.recorder.threads), 0)
        self.recorder.spawn_threads() 
        time.sleep(1)
        self.assertEqual(len(self.recorder.threads), 1)
        self.assertEqual(self.recorder.threads[0].device, '/dev/ttyS0')
        self.recorder.exiting.set()
        

    def tearDown(self):
        self.recorder.exiting.set()
        for thread in self.recorder.threads:
            thread.join()
        self.recorder.threads = []

if __name__ == "__main__":
    unittest.main()






"""Example working object patch: where testCall is a function in class
    SerialGrav.SerialRecorder
    @patch.object(SerialGrav.SerialRecorder, 'testCall')
    def test_mock_obj(self, patch_obj):
        patch_obj.return_value = 9
        thread = SerialGrav.SerialRecorder('ttyS0', threading.Event())
        val = thread.testCall()
        print("Mocked value: {}".format(val))
"""
