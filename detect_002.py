#!/usr/bin/env python3

"""Example module for Hailo Detection."""

#v0.02

import argparse
import cv2
from picamera2 import MappedArray, Picamera2, Preview
from picamera2.devices import Hailo
from picamera2.encoders import H264Encoder
from picamera2.outputs import CircularOutput
from libcamera import controls
import time
import os
import glob
import datetime
import shutil

# set variables
v_width    = 1456 # video width
v_height   = 1088 # video height
v_length   = 10   # seconds, minimum video length
pre_frames = 5    # seconds,  defines length of pre-trigger buffer
fps        = 25   # video frame rate
mp4_fps    = 25   # mp4 frame rate
mp4_timer  = 30   # seconds, convert h264s to mp4s after this time if no detections
mp4_anno   = 1    # apply timestamps to video, 1 = yes, 0 = no

# mp4_annotation parameters
colour    = (255, 255, 255)
origin    = (10, int(v_height - 50))
font      = cv2.FONT_HERSHEY_SIMPLEX
scale     = 1
thickness = 2

# shutdown time
sd_hour = 20
sd_mins = 30
auto_sd = 0 # set to 1 to shutdown at set time

# ram limit
ram_limit = 150 # stops recording if ram below this

# initialise
startup = time.monotonic()
start2  = time.monotonic()

def extract_detections(hailo_output, w, h, class_names, threshold=0.5):
    """Extract detections from the HailoRT-postprocess output."""
    results = []
    for class_id, detections in enumerate(hailo_output):
        for detection in detections:
            score = detection[4]
            if score >= threshold:
                y0, x0, y1, x1 = detection[:4]
                bbox = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
                results.append([class_names[class_id], bbox, score])
    return results


def draw_objects(request):
    current_detections = detections
    if current_detections:
        with MappedArray(request, "main") as m:
            for class_name, bbox, score in current_detections:
                x0, y0, x1, y1 = bbox
                label = f"{class_name} %{int(score * 100)}"
                cv2.rectangle(m.array, (x0, y0), (x1, y1), (0, 255, 0, 0), 2)
                cv2.putText(m.array, label, (x0 + 5, y0 + 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0, 0), 1, cv2.LINE_AA)

# apply timestamp to videos
def apply_timestamp(request):
  global mp4_anno
  if mp4_anno == 1:
      timestamp = time.strftime("%Y/%m/%d %T")
      with MappedArray(request, "main") as m:
          lst = list(origin)
          lst[0] += 365
          lst[1] -= 20
          end_point = tuple(lst)
          cv2.rectangle(m.array, origin, end_point, (0,0,0), -1) 
          cv2.putText(m.array, timestamp, origin, font, scale, colour, thickness)
          
if __name__ == "__main__":

    # find user
    Users  = []
    Users.append(os.getlogin())
    user   = Users[0]
    h_user = "/home/" + os.getlogin( )
    m_user = "/media/" + os.getlogin( )

    # check if clock synchronised
    if "System clock synchronized: yes" in os.popen("timedatectl").read().split("\n"):
        synced = 1
    else:
        synced = 0

    # Parse command-line arguments.
    parser = argparse.ArgumentParser(description="Detection Example")
    parser.add_argument("-m", "--model", help="Path for the HEF model.",
                        default="/usr/share/hailo-models/yolov8s_h8l.hef")
    parser.add_argument("-l", "--labels", default="/home/" + user + "/picamera2/examples/hailo/coco.txt",
                        help="Path to a text file containing labels.")
    parser.add_argument("-s", "--score_thresh", type=float, default=0.5,
                        help="Score threshold, must be a float between 0 and 1.")
    args = parser.parse_args()

    # Get the Hailo model, the input size it wants, and the size of our preview stream.
    with Hailo(args.model) as hailo:
        model_h, model_w, _ = hailo.get_input_shape()
        video_w, video_h    = v_width,v_height

        # Load class names from the labels file
        with open(args.labels, 'r', encoding="utf-8") as f:
            class_names = f.read().splitlines()

        # The list of detected objects to draw.
        detections = None

        # Configure and start Picamera2.
        with Picamera2() as picam2:
            main = {'size': (video_w, video_h), 'format': 'XRGB8888'}
            lores = {'size': (model_w, model_h), 'format': 'RGB888'}
            controls = {'FrameRate': fps}
            # use next line instead of above if using pi v3 camera
            #controls = {'FrameRate': fps,"AfMode": controls.AfModeEnum.Continuous,"AfTrigger": controls.AfTriggerEnum.Start}
            config = picam2.create_preview_configuration(main, lores=lores, controls=controls)
            picam2.configure(config)
            encoder = H264Encoder(4000000, repeat=True)
            encoder.output = CircularOutput(buffersize = pre_frames * fps)
            picam2.pre_callback = apply_timestamp
            picam2.start_preview(Preview.QTGL, x=0, y=0, width=model_w, height=model_h)
            picam2.start()
            picam2.start_encoder(encoder)
            encoding = False
            # enable the next line to show detections on video
            #picam2.pre_callback = draw_objects

            # Process each low resolution camera frame.
            while True:
                # get free ram space
                st = os.statvfs("/run/shm/")
                freeram = (st.f_bavail * st.f_frsize)/1100000
                
                # capture frame
                frame = picam2.capture_array('lores')

                # Run inference on the preprocessed frame
                results = hailo.run(frame)

                # Extract detections from the inference results
                detections = extract_detections(results[0], video_w, video_h, class_names, args.score_thresh)

                # detection
                if len(results[0][15]) != 0 or len(results[0][21]) != 0 or len(results[0][14]) != 0: # cat,bear or bird
                    start = time.monotonic()
                    start2 = time.monotonic()
                    # start recording
                    if not encoding:
                        now = datetime.datetime.now()
                        timestamp = now.strftime("%y%m%d%H%M%S")
                        encoder.output.fileoutput = "/run/shm/" + str(timestamp) + '.h264'
                        encoder.output.start()
                        encoding = True
                        print("New Detection", timestamp)

                # stop recording
                if encoding and time.monotonic() - start > v_length or freeram < ram_limit:
                    now = datetime.datetime.now()
                    timestamp2 = now.strftime("%y%m%d%H%M%S")
                    print("Stopped", timestamp2)
                    encoder.output.stop()
                    encoding = False
                    start2 = time.monotonic()

                # make mp4s
                if time.monotonic() - start2 > mp4_timer and not encoding:
                    start2 = time.monotonic()
                    # convert h264 to mp4
                    h264s = glob.glob('/run/shm/2*.h264')
                    h264s.sort(reverse = False)
                    for x in range(0,len(h264s)):
                        print(h264s[x][:-5] + '.mp4')
                        cmd = 'ffmpeg -framerate ' + str(mp4_fps) + ' -i ' + h264s[x] + " -c copy " + h264s[x][:-5] + '.mp4'
                        os.system(cmd)
                        os.remove(h264s[x])
                        print("Saved",h264s[x][:-5] + '.mp4')
                    Videos = glob.glob('/run/shm/2???????????.mp4')
                    Videos.sort()
                    # move Video RAM Files to SD card
                    for xx in range(0,len(Videos)):
                        if not os.path.exists(h_user + "/" + '/Videos/' + Videos[xx]):
                            shutil.move(Videos[xx], h_user + '/Videos/')

                # check if clock synchronised
                if "System clock synchronized: yes" in os.popen("timedatectl").read().split("\n"):
                    synced = 1
                else:
                    synced = 0
                # check current hour and shutdown
                now = datetime.datetime.now()
                sd_time = now.replace(hour=sd_hour, minute=sd_mins, second=0, microsecond=0)
                if now >= sd_time and time.monotonic() - startup > 300 and synced == 1 and auto_sd == 1:
                    # move mp4s to USB if present
                    USB_Files  = []
                    USB_Files  = (os.listdir(m_user))
                    if len(USB_Files) > 0:
                        usedusb = os.statvfs(m_user + "/" + USB_Files[0] + "/")
                        USB_storage = ((1 - (usedusb.f_bavail / usedusb.f_blocks)) * 100)
                    if len(USB_Files) > 0 and USB_storage < 90:
                        Videos = glob.glob(h_user + '/Videos/*.mp4')
                        Videos.sort()
                        for xx in range(0,len(Videos)):
                            movi = Videos[xx].split("/")
                            if not os.path.exists(m_user + "/" + USB_Files[0] + "/Videos/" + movi[4]):
                                shutil.move(Videos[xx],m_user + "/" + USB_Files[0] + "/Videos/")
                    time.sleep(5)
                    # shutdown
                    os.system("sudo shutdown -h now")


