import io
import cv2
import numpy as np
from threading import Condition

class CameraProcessor:
    def __init__(self):
        self.output = StreamingOutput()
        self.lower_red1 = np.array([0, 120, 70])
        self.upper_red1 = np.array([10, 255, 255])
        self.lower_red2 = np.array([170, 120, 70])
        self.upper_red2 = np.array([180, 255, 255])

    class StreamingOutput(io.BufferedIOBase):
        def __init__(self):
            self.frame = None
            self.condition = Condition()
            self.red_count = 0

        def write(self, buf):
            with self.condition:
                self.frame = buf
                self.condition.notify_all()

        def set_red_count(self, count):
            self.red_count = count

        def get_red_count(self):
            return self.red_count

    def process_frame(self, frame):
        np_frame = np.frombuffer(frame, dtype=np.uint8)
        img = cv2.imdecode(np_frame, cv2.IMREAD_COLOR)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        
        mask1 = cv2.inRange(hsv, self.lower_red1, self.upper_red1)
        mask2 = cv2.inRange(hsv, self.lower_red2, self.upper_red2)
        mask = mask1 | mask2
        
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        red_count = 0
        
        for contour in contours:
            if cv2.contourArea(contour) > 500:
                x, y, w, h = cv2.boundingRect(contour)
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
                red_count += 1
                
        self.output.set_red_count(red_count)
        _, encoded_frame = cv2.imencode('.jpg', img)
        return encoded_frame.tobytes()