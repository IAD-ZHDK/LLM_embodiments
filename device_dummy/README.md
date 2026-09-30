# Device Dummy Demo

1. Set `communicationMethod = "WiFi"` in `config.toml`.
2. Start the backend in one terminal:

   ```bash
   ./run.sh
   ```

3. In another terminal, install the simulator dependencies and connect it:

   ```bash
   backend/venv/bin/pip install -r device_dummy/requirements.txt
   backend/venv/bin/python device_dummy/device_dummy.py --url ws://127.0.0.1:3000/device
   ```

Type `temp 23.5` in the simulator terminal to send temperature data. Use `--no-mic` to disable microphone streaming.
