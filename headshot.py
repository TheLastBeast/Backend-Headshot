import time
import board
import busio
from adafruit_tca9548a import TCA9548A
import adafruit_mpu6050
import wifi
import socketpool
import microcontroller
import json

# Function to initialize I2C and sensors
def initialize_sensors():
    global multiplexer, mpu_sensors
    try:
        # Initialize I2C
        print("Initializing I2C...")
        i2c = busio.I2C(board.GP5, board.GP4)
        print("I2C initialized.")

        # Initialize the TCA9548A multiplexer
        print("Initializing TCA9548A multiplexer...")
        multiplexer = TCA9548A(i2c, address=0x70)
        print("Multiplexer initialized.")

        # Initialize MPU6050 sensors on different channels of the multiplexer
        print("Initializing MPU6050 sensors...")
        mpu_sensors = [
            adafruit_mpu6050.MPU6050(multiplexer[0]),  # First sensor on channel 0
            adafruit_mpu6050.MPU6050(multiplexer[1]),  # Second sensor on channel 1
            adafruit_mpu6050.MPU6050(multiplexer[2])   # Third sensor on channel 2
        ]
        
        # Set the accelerometer sensitivity to ±2g for higher precision
        for mpu in mpu_sensors:
            mpu.accelerometer_range = adafruit_mpu6050.Range.RANGE_2_G  # Most sensitive setting (±2g)
        
        print("MPU6050 sensors initialized with sensitivity ±2g.")
        return True

    except Exception as e:
        print(f"Error during sensor initialization: {e}")
        return False

# Attempt to initialize the sensors
while not initialize_sensors():
    print("Retrying sensor initialization in 5 seconds...")
    time.sleep(5)  # Retry after 5 seconds

# Set up the Pico W in Access Point (AP) mode
ssid = "PicoW-AccessPoint"  # Network name
password = "pico1234"  # Password

# Enable the Pico W as an access point
try:
    print("Setting up access point...")
    wifi.radio.start_ap(ssid, password=password, channel=6)
    ap_ip = wifi.radio.ipv4_address_ap
    print(f"Access point started. IP address: {ap_ip}")
except Exception as e:
    print(f"Failed to start access point: {e}")
    microcontroller.reset()  # Reset if AP fails to start

# Create a socket pool and start a simple server
try:
    print("Creating socket pool...")
    pool = socketpool.SocketPool(wifi.radio)
    server_socket = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
    print(f"Binding to {ap_ip} on port 80...")
    server_socket.bind((str(ap_ip), 80))  # Bind to port 80
    server_socket.listen(1)
    server_socket.settimeout(10000)  # Timeout after 100 seconds if no connection
    print(f"Listening for connections on {ap_ip}")
except Exception as e:
    print(f"Failed to bind socket: {e}")
    microcontroller.reset()  # Reset if socket fails to bind

# Function to get sensor data, print all to console, but only send total g-force if it exceeds the threshold
def get_sensor_data_sse():
    total_accel_g_values = []
    
    for i, mpu in enumerate(mpu_sensors):
        try:
            accel_data = mpu.acceleration
            # Convert acceleration to g-force values
            accel_g_precise = [a / 9.81 for a in accel_data]
            total_accel_g = abs(accel_g_precise[0]) + abs(accel_g_precise[1]) + abs(accel_g_precise[2])

            # Always print all sensor data to console
            print(f"Sensor {i + 1}: accel_g_precise = {accel_g_precise}, total_accel_g = {total_accel_g}")
            
            # Only store total g-force if it exceeds the threshold
            if total_accel_g > 2:  # Set your threshold here
                total_accel_g_values.append({
                    "sensor": i + 1,
                    "total_acceleration_g": total_accel_g
                })

        except Exception as e:
            print(f"Error reading sensor {i + 1}: {e}")

    # Only return the g-force values that exceeded the threshold
    if total_accel_g_values:
        return json.dumps(total_accel_g_values)
    else:
        return None  # Return None if no data exceeds the threshold

# Main loop to serve both HTML, SSE, and /sensor_data
while True:
    try:
        print("Waiting for a client connection...")
        client, addr = server_socket.accept()
        print(f"Client connected from {addr}")

        buffer = bytearray(2048)
        client.recv_into(buffer)
        request_str = str(buffer, 'utf-8').strip()
        print(f"Received request: {request_str}")

        # Serve SSE if the client is requesting /events
        if "GET /events" in request_str:
            print("Valid SSE request received. Streaming data...")
            client.send("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n".encode('utf-8'))

            # Keep the connection alive and send data continuously
            try:
                while True:
                    sensor_data_json = get_sensor_data_sse()
                    if sensor_data_json:
                        # Send only when total g-force exceeds the threshold
                        sse_message = f"data: {sensor_data_json}\n\n"  # Follow SSE format
                        try:
                            client.send(sse_message.encode('utf-8'))
                            print(f"Data sent: {sensor_data_json}")  # Debugging message
                        except OSError as send_error:
                            print(f"Error sending data: {send_error}")
                            break  # Stop sending if there's a connection issue (Broken pipe, client disconnect)
                    time.sleep(1)  # Send data every 1 second
            except Exception as e:
                print(f"Error during communication: {e}")
            finally:
                print("Closing client connection...")
                client.close()  # Ensure the socket is closed properly
        
        # Serve /sensor_data for Flutter or other requests
        elif "GET /sensor_data" in request_str:
            print("Serving sensor data as JSON...")
            sensor_data_json = get_sensor_data_sse()
            if sensor_data_json:
                response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{sensor_data_json}"
                client.send(response.encode('utf-8'))
            else:
                response = "HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"
                client.send(response.encode('utf-8'))
            client.close()

        # Serve a basic HTML page for regular / requests
        elif "GET / " in request_str:
            print("Serving HTML page...")
            html_page = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Pico W Server</title>
            </head>
            <body>
                <h1>Pico W SSE Server</h1>
                <p>Visit <a href='/events'>/events</a> for real-time sensor data via Server-Sent Events (SSE).</p>
            </body>
            </html>
            """
            response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n" + html_page
            client.send(response.encode('utf-8'))
            print("HTML page sent, closing connection.")
            client.close()
        
        else:
            print(f"Invalid request: {request_str}. Closing connection.")
            client.close()

    except Exception as e:
        print(f"Error during communication: {e}")
        if client:
            client.close()
    time.sleep(1)

