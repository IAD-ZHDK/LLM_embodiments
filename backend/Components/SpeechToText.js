import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

class SpeechToText {

    constructor(speechRecievedCallback = this.defaultCallback, speechToTextModel = 'vosk-model-small-en-us-0.15', sttBackend = 'vosk') {
        this.speechToTextModel = speechToTextModel;
        this.sttBackend = sttBackend;
        this.speechRecievedCallback = speechRecievedCallback;
        this.py = spawn('python3', ['scriptSTT.py', '--backend', this.sttBackend.toString(), '--model', this.speechToTextModel.toString()], {
            cwd: path.join(__dirname, '../../python') // Correct relative path
        });

        // Listen for any message from Python
        this.py.stdout.on('data', (data) => {
            data.toString().split('\n').filter(Boolean).forEach(line => {
                let msg;
                try {
                    msg = JSON.parse(line);
                } catch (e) {
                    console.error('Failed to parse Python message (non-JSON):', line);
                    return;
                }
                try {
                    this.speechRecievedCallback(msg);
                } catch (e) {
                    console.error('Error handling Python message:', msg, e);
                }
            });
        });

        // Optional: Handle Python errors and exit
        this.py.stderr.on('data', (data) => {
            console.error('Python:', data.toString());
        });
        this.py.on('close', (code) => {
            console.log(`Python process exited with code ${code}`);
        });

    }
    pause() {
        return this.sendMessage({ STT: 'pause' });
    }

    resume() {
        return this.sendMessage({ STT: 'resume' });
    }

    sendMessage(message) {
        return new Promise((resolve, reject) => {
            const onData = (data) => {
                this.py.stdout.off('data', onData); // Remove listener after use
            };
            this.py.stdout.on('data', onData);
            this.py.stdin.write(JSON.stringify(message) + '\n');
        });
    }

    defaultCallback(data) {
        console.log("default callback function: " + data);
    }


} export default SpeechToText;