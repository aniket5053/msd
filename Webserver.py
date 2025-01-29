import io
import logging
import socketserver
from http import server
from threading import Condition
import cv2
import numpy as np
import time
import board
import adafruit_sht4x

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

sht = adafruit_sht4x.SHT4x(board.I2C())
PAGE = """\
<html>
<head>
<title>BeeCam - Picamera2 MJPEG Streaming</title>
<style>
    body {
        background-color: #FFF8E1;
        font-family: 'Arial', sans-serif;
        text-align: center;
        color: #3E2723;
    }
    h1 {
        font-size: 3em;
        color: #F57F17;
        font-family: 'Comic Sans MS', sans-serif;
    }
    #red-count {
        font-size: 1.5em;
        margin-top: 20px;
        font-weight: bold;
        color: #F44336;
    }
    .bee-icon {
        width: 100px;
        height: 100px;
        margin-top: 20px;
    }
    img {
        border: 5px solid #F57F17;
        border-radius: 15px;
    }
</style>
<script type="text/javascript">
    function updateRedCount() {
        fetch('/count')
        .then(response => response.json())
        .then(data => {
            document.getElementById('red-count').innerText = 'Red Objects Detected: ' + data.count;
        });
    }

    function updateSensors() {
        fetch('/sensors')
        .then(response => response.json())
        .then(data => {
            document.getElementById('temperature').innerText = 'Temperature: ' + data.temperature + ' °C';
            document.getElementById('humidity').innerText = 'Humidity: ' + data.humidity + ' %';
        });
    }

    setInterval(updateRedCount, 500);  // Update the count every 500ms
    setInterval(updateSensors, 2000); // Update the sensors every 2 seconds
</script>
</head>
<body>
    <h1>BeeCam - Live Stream</h1>
    <p id="red-count">Red Objects Detected: 0</p>
    <p id="temperature">Temperature: </p>
    <p id="humidity">Humidity: </p>
    <img src="stream.mjpg" width="640" height="480" />
</body>
</html>
"""

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.red_count = 0  # Initialize red count

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

    def set_red_count(self, count):
        self.red_count = count
        print(f"Red Objects Count Updated: {self.red_count}")  # Print the red object count in terminal

    def get_red_count(self):
        return self.red_count

class StreamingHandler(server.BaseHTTPRequestHandler):
    is_streaming = False  # Flag to indicate if streaming is active

    def do_GET(self):
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            if not StreamingHandler.is_streaming:  # Start streaming when first client connects
                StreamingHandler.is_streaming = True
                print("Streaming started...")  # Output to terminal when streaming starts
                self.send_response(200)
                self.send_header('Age', 0)
                self.send_header('Cache-Control', 'no-cache, private')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
                self.end_headers()

                try:
                    while StreamingHandler.is_streaming:
                        with output.condition:
                            output.condition.wait()
                            frame = output.frame

                        # Convert the frame to numpy array for OpenCV processing
                        np_frame = np.frombuffer(frame, dtype=np.uint8)
                        img = cv2.imdecode(np_frame, cv2.IMREAD_COLOR)

                        # Convert the image to HSV
                        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

                        # Define the red color range in HSV
                        lower_red = np.array([0, 120, 70])
                        upper_red = np.array([10, 255, 255])
                        mask1 = cv2.inRange(hsv, lower_red, upper_red)

                        lower_red = np.array([170, 120, 70])
                        upper_red = np.array([180, 255, 255])
                        mask2 = cv2.inRange(hsv, lower_red, upper_red)

                        # Combine the two masks to capture red from both ranges
                        mask = mask1 | mask2

                        # Find contours in the mask
                        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                        # Draw rectangles around detected red regions
                        red_count = 0
                        for contour in contours:
                            if cv2.contourArea(contour) > 500:  # Minimum area to avoid noise
                                x, y, w, h = cv2.boundingRect(contour)
                                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)  # Green rectangle
                                red_count += 1  # Increment the red object count

                        # Set the red count to be accessible
                        output.set_red_count(red_count)

                        # Encode the result to send as MJPEG stream
                        _, encoded_frame = cv2.imencode('.jpg', img)
                        frame = encoded_frame.tobytes()

                        # Send the processed frame
                        self.wfile.write(b'--FRAME\r\n')
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', len(frame))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
                except Exception as e:
                    logging.warning(
                        'Removed streaming client %s: %s',
                        self.client_address, str(e))
                finally:
                    StreamingHandler.is_streaming = False  # Stop streaming when client disconnects
                    print("Streaming stopped...")  # Output to terminal when streaming stops
            else:
                self.send_error(404)
                self.end_headers()
        elif self.path == '/count':
            # Return the current red object count as JSON
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            count_data = {'count': output.get_red_count()}
            print(f"Fetching Red Objects Count: {count_data['count']}")  # Print the fetched red object count
            self.wfile.write(bytes(str(count_data).replace("'", '"'), 'utf-8'))  # Convert dict to JSON string
        elif self.path == '/sensors':
            # Return the current temperature and humidity as JSON
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            sensors_data = {'temperature': sht.temperature, 'humidity': sht.relative_humidity}
            print(f"Fetching Sensors Data: Temperature = {sensors_data['temperature']} °C, Humidity = {sensors_data['humidity']} %")  # Print the fetched sensor data
            self.wfile.write(bytes(str(sensors_data).replace("'", '"'), 'utf-8'))  # Convert dict to JSON string
        else:
            self.send_error(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
output = StreamingOutput()
picam2.start_recording(JpegEncoder(), FileOutput(output))

try:
    address = ('', 7123)
    server = StreamingServer(address, StreamingHandler)
    server.serve_forever()
finally:
    picam2.stop_recording()
