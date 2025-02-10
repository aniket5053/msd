# main.py
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput
from camera import CameraProcessor
from sensors import SensorManager
from server import WebServer

def main():
    camera_processor = CameraProcessor()
    sensor_manager = SensorManager()
    
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
    picam2.start_recording(JpegEncoder(), FileOutput(camera_processor.output))
    
    web_server = WebServer(camera_processor, sensor_manager)
    server = web_server.StreamingServer(
        ('', 7123), 
        lambda *args, **kwargs: web_server.StreamingHandler(*args, server_ref=web_server, **kwargs)
    )
    
    try:
        print("Server running on port 7123")
        server.serve_forever()
    finally:
        picam2.stop_recording()

if __name__ == "__main__":
    main()