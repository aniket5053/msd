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
import json
from collections import deque

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

sht = adafruit_sht4x.SHT4x(board.I2C())
data_queue = deque(maxlen=100)  # Store last 100 readings for sliding window

PAGE = """
<html>
<head>
<title>Live Environment Monitoring</title>
<style>
    body {
        background-color: #f4f4f4;
        font-family: 'Arial', sans-serif;
        text-align: center;
        color: #333;
    }
    h1 {
        font-size: 2.5em;
        color: #333;
    }
    #data-container {
        margin-top: 20px;
        font-size: 1.2em;
        font-weight: bold;
    }
    canvas {
        width: 90%;
        max-width: 800px;
        margin-top: 20px;
        border: 1px solid #ccc;
    }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
    let tempData = [];
    let humidityData = [];
    let labels = [];
    let chart;

    function updateSensors() {
        fetch('/sensors')
        .then(response => response.json())
        .then(data => {
            document.getElementById('temperature').innerText = 'Temperature: ' + data.temperature + ' °C';
            document.getElementById('humidity').innerText = 'Humidity: ' + data.humidity + ' %';
            
            if (labels.length >= 100) {
                labels.shift();
                tempData.shift();
                humidityData.shift();
            }
            labels.push(new Date().toLocaleTimeString());
            tempData.push(data.temperature);
            humidityData.push(data.humidity);
            
            chart.update();
        });
    }

    function initChart() {
        const ctx = document.getElementById('sensorChart').getContext('2d');
        chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Temperature (°C)',
                        data: tempData,
                        borderColor: 'red',
                        fill: false
                    },
                    {
                        label: 'Humidity (%)',
                        data: humidityData,
                        borderColor: 'blue',
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                animation: { duration: 500 }, // Faster updates
                scales: {
                    x: { type: 'linear', position: 'bottom' }
                }
            }
        });
    }

    window.onload = function() {
        initChart();
        setInterval(updateSensors, 1000);
    };
</script>
</head>
<body>
    <h1>Live Environment Monitoring</h1>
    <p id="temperature">Temperature: --</p>
    <p id="humidity">Humidity: --</p>
    <img src="stream.mjpg" width="640" height="480" />
    <canvas id="sensorChart"></canvas>
</body>
</html>
"""

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

class StreamingHandler(server.BaseHTTPRequestHandler):
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
            self.send_response(200)
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning('Streaming client disconnected: %s', str(e))
        elif self.path == '/sensors':
            temperature = round(sht.temperature, 2)
            humidity = round(sht.relative_humidity, 2)
            data_queue.append({'temperature': temperature, 'humidity': humidity})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'temperature': temperature, 'humidity': humidity}).encode('utf-8'))
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
