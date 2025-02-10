# camera.py
import io
import cv2
import numpy as np
from threading import Condition

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.red_count = 0  # Track red object count

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

    def set_red_count(self, count):
        with self.condition:
            self.red_count = count

    def get_red_count(self):
        with self.condition:
            return self.red_count

class CameraProcessor:
    def __init__(self):
        self.output = StreamingOutput()
        # HSV color ranges for red detection
        self.lower_red1 = np.array([0, 120, 70])
        self.upper_red1 = np.array([10, 255, 255])
        self.lower_red2 = np.array([170, 120, 70])
        self.upper_red2 = np.array([180, 255, 255])

    def process_frame(self, frame):
        """Process a camera frame for red object detection"""
        try:
            # Convert frame to OpenCV format
            np_frame = np.frombuffer(frame, dtype=np.uint8)
            img = cv2.imdecode(np_frame, cv2.IMREAD_COLOR)

            # Convert to HSV color space
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

            # Create masks for red color detection
            mask1 = cv2.inRange(hsv, self.lower_red1, self.upper_red1)
            mask2 = cv2.inRange(hsv, self.lower_red2, self.upper_red2)
            combined_mask = mask1 | mask2

            # Find contours in the mask
            contours, _ = cv2.findContours(
                combined_mask, 
                cv2.RETR_EXTERNAL, 
                cv2.CHAIN_APPROX_SIMPLE
            )

            red_count = 0
            # Draw rectangles around detected objects
            for contour in contours:
                if cv2.contourArea(contour) > 500:  # Filter small contours
                    x, y, w, h = cv2.boundingRect(contour)
                    cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    red_count += 1

            # Update the red count in output
            self.output.set_red_count(red_count)

            # Encode the processed frame
            _, encoded_frame = cv2.imencode('.jpg', img)
            return encoded_frame.tobytes()

        except Exception as e:
            print(f"Frame processing error: {e}")
            return frame  # Return original frame if processing fails