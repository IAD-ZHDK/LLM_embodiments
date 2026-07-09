import { exec } from 'child_process';
import fs from "fs";
import path from "path";
import NodeWebcam from "node-webcam";

export async function captureImage() {
  console.log("Capturing image...");

  // Create scratch_files directory if it doesn't exist
  const scratchDir = "scratch_files";
  if (!fs.existsSync(scratchDir)) {
    fs.mkdirSync(scratchDir);
  }

  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const fileName = path.join(scratchDir, `test_image_${timestamp}`);
  const fullFileName = `${fileName}.jpg`;

  // Better platform detection logic
  const platform = process.platform;
  const isArm = process.arch === 'arm' || process.arch === 'arm64';

  // Only use Pi-specific methods on Linux ARM devices
  const isRaspberryPi = platform === 'linux' && isArm;

  console.log(`Detected platform: ${platform}, architecture: ${process.arch}`);
  console.log(`Using ${isRaspberryPi ? 'Raspberry Pi' : 'standard'} capture method`);

  if (isRaspberryPi) {
    return captureWithDirectCommands(fullFileName);
  } else {
    return captureWithNodeWebcam(fileName);
  }
}
// For non-Pi devices (Mac, Windows, etc)
function captureWithNodeWebcam(fileName) {
  console.log("Using node-webcam for image capture...");

  const opts = {
    width: 640,
    height: 480,
    quality: 100,
    output: "jpeg",
    callbackReturn: "base64",
  };

  const Webcam = NodeWebcam.create(opts);

  return new Promise((resolve, reject) => {
    Webcam.capture(fileName, async function (err, data) {
      if (err) {
        console.error("Webcam error:", err);
        return reject(err);
      }

      // Remove prefix if present
      let cleanBase64 = data;
      if (data.startsWith("data:image/jpeg;base64,")) {
        cleanBase64 = data.replace(/^data:image\/jpeg;base64,/, "");
      } else if (data.startsWith("data:image/png;base64,")) {
        cleanBase64 = data.replace(/^data:image\/png;base64,/, "");
      }

      console.log("Image captured with node-webcam");

      // Prepare base64 string for OpenAI API
      const base64Image = `data:image/jpeg;base64,${cleanBase64}`;
      const justFilename = path.basename(fileName) + '.jpg';
      resolve({
        fileName: justFilename,
        description: "Camera Image",
        value: base64Image
      });

      /*
      try {
        const llm = new LLMAPI(config, functionHandler);
        const result = await llm.sendImage(base64Image, "user");
        console.log("Vision API result received");
        resolve(result);
      } catch (apiErr) {
        console.error("Error with Vision API:", apiErr);
        reject(apiErr);
      }
        */
    });
  });
}

// For Raspberry Pi devices
function captureWithDirectCommands(fileName) {
  console.log("Using direct commands for Raspberry Pi image capture...");

  return new Promise((resolve, reject) => {
    // Try different methods to capture an image
    tryCaptureMethods(fileName, 0, async (success) => {
      if (!success) {
        return reject(new Error("All camera capture methods failed"));
      }

      try {
        // Read the captured image file
        const imageBuffer = fs.readFileSync(fileName);
        const base64Image = `data:image/jpeg;base64,${imageBuffer.toString('base64')}`;

        console.log(`Image captured and saved as: ${fileName}`);
        const justFilename = path.basename(fileName);

        resolve({
          fileName: justFilename,
          description: "Camera Image",
          value: base64Image
        });
      } catch (fileErr) {
        console.error("Error processing image file:", fileErr);
        reject(fileErr);
      }
      /*
      try {
      // Send to Vision API
      const llm = new LLMAPI(config, functionHandler);
      const result = await llm.sendImage(base64Image, "user");
      console.log("Vision API result received");
      resolve(result);
    } catch (fileErr) {
      console.error("Error processing image file:", fileErr);
      reject(fileErr);
    }
      */
    });
  });
}

// Try multiple camera capture methods in sequence
function tryCaptureMethods(fileName, methodIndex, callback) {
  const methods = [
    // Method 1: rpicam-jpeg (replacement for libcamera-still on newer Pi OS)
    `rpicam-jpeg -o ${fileName} --width 640 --height 480 --timeout 1 --nopreview`,

    // Method 2: libcamera-still (older Pi OS)
    `libcamera-still -o ${fileName} --width 640 --height 480 --immediate --nopreview`,

    // Method 3: raspistill (legacy Pi OS)
    `raspistill -o ${fileName} -w 640 -h 480 -n`,

    // Method 4: fswebcam with YUYV format (often works when default fails)
    `fswebcam -r 640x480 --no-banner --set "pixel_format=YUYV" ${fileName}`,

    // Method 5: fswebcam with MJPEG format
    `fswebcam -r 640x480 --no-banner --set "pixel_format=MJPEG" ${fileName}`,

    // Method 6: fswebcam normal with lower resolution
    `fswebcam -r 320x240 --no-banner --jpeg 85 ${fileName}`,

    // Method 7: fswebcam with lowest resolution as last resort
    `fswebcam -r 160x120 --no-banner --jpeg 85 ${fileName}`
  ];

  if (methodIndex >= methods.length) {
    console.error("All camera capture methods failed");
    return callback(false);
  }

  console.log(`Trying camera method ${methodIndex + 1}/${methods.length}: ${methods[methodIndex]}`);

  exec(methods[methodIndex], (err, stdout, stderr) => {
    if (err) {
      console.log(`Method ${methodIndex + 1} failed: ${err.message}`);
      console.log(`stderr: ${stderr}`);
      // Try next method
      tryCaptureMethods(fileName, methodIndex + 1, callback);
    } else {
      // Check if file exists and has content
      try {
        const stats = fs.statSync(fileName);
        if (stats.size > 100) { // File exists and has some content
          console.log(`Successfully captured image with method ${methodIndex + 1}`);
          callback(true);
        } else {
          console.log(`Method ${methodIndex + 1} created an empty or corrupt file`);
          // Try next method
          tryCaptureMethods(fileName, methodIndex + 1, callback);
        }
      } catch (e) {
        console.log(`Method ${methodIndex + 1} failed to create a valid file`);
        // Try next method
        tryCaptureMethods(fileName, methodIndex + 1, callback);
      }
    }
  });
}