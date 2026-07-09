import fs from 'fs';
import path from 'path';
import os from 'os';
import crypto from 'crypto';
import { spawn, exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

class WiFiManager {
    constructor() {
        this.connectionName = 'auto-connect-wifi';
    }

    // Helper: only supported on Linux where NetworkManager (nmcli) is available
    _isPlatformSupported() {
        return process.platform === 'linux';
    }

    // Helper wrapper to call execAsync with a timeout and consistent error handling
    async _exec(cmd, opts = {}) {
        const timeout = opts.timeout || 15000; // default 15s
        try {
            return await execAsync(cmd, { timeout });
        } catch (e) {
            // include stdout/stderr when available to help diagnostics
            const out = e.stdout ? `\nSTDOUT:\n${e.stdout}` : '';
            const err = e.stderr ? `\nSTDERR:\n${e.stderr}` : '';
            throw new Error(`Command failed: ${cmd} -> ${e.message}${out}${err}`);
        }
    }

    // --- secret store helpers ---
    _secretsFilePath() {
        return path.join(os.homedir(), '.config', 'llm_embodiments', 'wifi_secrets.json');
    }

    _ensureSecretsDir() {
        const dir = path.dirname(this._secretsFilePath());
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    }

    _getEncryptionKey() {
        // Derive key from machine-id (or hostname) + SALT env or fallback
        let machineId = null;
        try {
            machineId = fs.readFileSync('/etc/machine-id', 'utf8').trim();
        } catch (e) {
            machineId = os.hostname();
        }
        const SALT = process.env.WIFI_SECRET_SALT || 'CHANGE_THIS_RANDOM_SALT';
        return crypto.createHash('sha256').update(machineId + SALT).digest(); // 32 bytes
    }

    _encryptSecret(plainText) {
        const key = this._getEncryptionKey();
        const iv = crypto.randomBytes(12); // GCM nonce
        const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
        const encrypted = Buffer.concat([cipher.update(plainText, 'utf8'), cipher.final()]);
        const tag = cipher.getAuthTag();
        return `${iv.toString('hex')}.${tag.toString('hex')}.${encrypted.toString('hex')}`;
    }

    _decryptSecret(blob) {
        if (!blob) return null;
        try {
            const [ivHex, tagHex, encHex] = blob.split('.');
            const iv = Buffer.from(ivHex, 'hex');
            const tag = Buffer.from(tagHex, 'hex');
            const enc = Buffer.from(encHex, 'hex');
            const key = this._getEncryptionKey();
            const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
            decipher.setAuthTag(tag);
            const dec = Buffer.concat([decipher.update(enc), decipher.final()]);
            return dec.toString('utf8');
        } catch (e) {
            console.error('Failed to decrypt secret:', e.message);
            return null;
        }
    }

    saveSecret(ssid, password) {
        try {
            this._ensureSecretsDir();
            const fp = this._secretsFilePath();
            let data = {};
            if (fs.existsSync(fp)) {
                data = JSON.parse(fs.readFileSync(fp, 'utf8') || '{}');
            }
            data[ssid] = this._encryptSecret(password);
            fs.writeFileSync(fp, JSON.stringify(data, null, 2), { mode: 0o600 });
        } catch (e) {
            console.error('Error saving WiFi secret:', e.message);
        }
    }

    loadSecret(ssid) {
        try {
            const fp = this._secretsFilePath();
            if (!fs.existsSync(fp)) return null;
            const data = JSON.parse(fs.readFileSync(fp, 'utf8') || '{}');
            if (!data[ssid]) return null;
            return this._decryptSecret(data[ssid]);
        } catch (e) {
            console.error('Error loading WiFi secret:', e.message);
            return null;
        }
    }

    // Return list of SSIDs stored in the local encrypted secrets file
    listSavedSSIDs() {
        try {
            const fp = this._secretsFilePath();
            if (!fs.existsSync(fp)) return [];
            const data = JSON.parse(fs.readFileSync(fp, 'utf8') || '{}');
            return Object.keys(data || {});
        } catch (e) {
            console.error('Error listing saved SSIDs:', e.message);
            return [];
        }
    }

    // Try to activate an existing NetworkManager connection profile matching the SSID
    async tryActivateExistingProfile(ssid) {
        if (!this._isPlatformSupported()) return false;
        try {
            const { stdout } = await this._exec('nmcli -t -f NAME,TYPE connection show');
            const lines = stdout.trim().split('\n').filter(l => l.length > 0);
            for (const line of lines) {
                const [name, type] = line.split(':');
                if (!name || !type) continue;
                if (type === '802-11-wireless' && (name === ssid || name.includes(ssid))) {
                    try {
                        await this._exec(`sudo nmcli connection up "${name}"`);
                        console.log(`Activated existing NM profile: ${name}`);
                        return true;
                    } catch (e) {
                        // continue trying other profiles
                        console.log(`Failed to activate profile ${name}: ${e.message}`);
                    }
                }
            }
            return false;
        } catch (e) {
            console.error('Error while trying to activate existing profile:', e.message);
            return false;
        }
    }

    // Helper: determine the wifi interface name (e.g., wlan0, wlp3s0) via nmcli
    async _getWifiInterface() {
        if (!this._isPlatformSupported()) return null;
        try {
            const { stdout } = await this._exec('nmcli -t -f DEVICE,TYPE device status');
            const lines = stdout.trim().split('\n');
            for (const line of lines) {
                const [device, type] = line.split(':');
                if (type === 'wifi') return device;
            }
            return null;
        } catch (e) {
            console.error('Error detecting wifi interface:', e.message);
            return null;
        }
    }
    // --- end secret helpers ---

    /**
     * Check if WiFi is connected (not just enabled) - improved to distinguish from Ethernet
     */
    async isConnected() {
        try {
            if (!this._isPlatformSupported()) return false;
            const { stdout } = await this._exec('nmcli -t -f TYPE,STATE connection show --active');
            const lines = stdout.trim().split('\n');
            const wifiConnected = lines.some(line =>
                line.startsWith('802-11-wireless:') && line.includes(':activated')
            );
            return wifiConnected;
        } catch (error) {
            console.error('Error checking WiFi status:', error.message);
            return false;
        }
    }

    /**
     * Get current WiFi connection status - improved to exclude Ethernet
     */
    async getConnectionStatus() {
        try {
            if (!this._isPlatformSupported()) return { connected: false, activeConnections: [] };
            const { stdout } = await this._exec('nmcli -t -f NAME,TYPE,STATE connection show --active');
            const lines = stdout.trim().split('\n');
            const activeWiFiConnections = lines
                .filter(line => line.includes(':802-11-wireless:activated'))
                .map(line => line.split(':')[0]);

            return {
                connected: activeWiFiConnections.length > 0,
                activeConnections: activeWiFiConnections
            };
        } catch (error) {
            console.error('Error getting connection status:', error.message);
            return { connected: false, activeConnections: [] };
        }
    }

    /**
     * Connect to a regular WPA2/WPA3 network
     */
    async connectToWPA(ssid, password) {
        try {
            console.log(`Attempting to connect to WPA network: ${ssid}`);

            if (!this._isPlatformSupported()) {
                console.log('Platform does not support nmcli; skipping WiFi connect');
                return { success: false, message: 'Platform does not support nmcli' };
            }

            // ...existing code...

            // If persistence is disabled, remove any existing connection to create an ephemeral one
            if ((process.env.WIFI_PERSISTENCE || 'true').toLowerCase() === 'false') {
                await this.removeConnection(this.connectionName);
            }

            // Prefer using `nmcli device wifi connect` which provides the secret
            // to NetworkManager at activation time (avoids 'Secrets were required' errors).
            const wifiIf = await this._getWifiInterface();
            let deviceConnectCmd = `sudo nmcli device wifi connect "${ssid}" password "${password}"`;
            if (wifiIf) deviceConnectCmd = `sudo nmcli device wifi connect "${ssid}" ifname ${wifiIf} password "${password}"`;
            try {
                await this._exec(deviceConnectCmd);
                console.log(`Successfully connected to ${ssid} (via device wifi connect)`);
            } catch (connectErr) {
                console.log(`device wifi connect failed: ${connectErr.message}. Falling back to connection add/modify/up flow.`);

                // create connection without storing secret; prefer explicit interface if available
                let addCmd = `sudo nmcli connection add con-name "${this.connectionName}" type wifi ssid "${ssid}" ipv4.method auto connection.autoconnect yes`;
                if (wifiIf) addCmd = `sudo nmcli connection add con-name "${this.connectionName}" type wifi ifname ${wifiIf} ssid "${ssid}" ipv4.method auto connection.autoconnect yes`;
                await this._exec(addCmd);

                // store secret via nmcli modify (this writes the secret so NM can activate)
                // For WPA-PSK:
                await this._exec(`sudo nmcli connection modify "${this.connectionName}" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${password}"`);

                // attempt to bring the connection up (NM now has the secret to use)
                await this._exec(`sudo nmcli connection up "${this.connectionName}"`);
            }
            console.log(`Successfully connected to ${ssid}`);

            // By default keep the NetworkManager profile persistent so the connection
            // remains active after this process exits. To make the connection
            // ephemeral and scrub secrets from NM set WIFI_PERSISTENCE=false
            const wifiPersistence = (process.env.WIFI_PERSISTENCE || 'true').toLowerCase();
            if (wifiPersistence === 'false') {
                // mark the secret as not stored and scrub plaintext from the system file
                // (for PSK use wifi-sec.psk-flags 1; for EAP use 802-1x.password-flags 1)
                await this._exec(`sudo nmcli connection modify "${this.connectionName}" 802-1x.password-flags 1`);
                await this._exec(
                    `sudo sed -i -e '/^\\s*psk=/d' -e '/^\\s*password=/d' /etc/NetworkManager/system-connections/"${this.connectionName}".nmconnection || true`
                );

                // persist local encrypted copy
                this.saveSecret(ssid, password);
            } else {
                // Keep profile persistent and save encrypted local copy for future runs
                this.saveSecret(ssid, password);
            }
            return { success: true, message: `Connected to ${ssid}` };
        } catch (error) {
            console.error('Error connecting to WPA network:', error.message);
            return { success: false, message: error.message };
        }
    }

    /**
     * Connect to WPA2 Enterprise network
     */
    async connectToWPA2Enterprise(ssid, username, password) {
        try {
            console.log(`Attempting to connect to WPA2 Enterprise network: ${ssid}`);
            if (!this._isPlatformSupported()) {
                console.log('Platform does not support nmcli; skipping WiFi connect');
                return { success: false, message: 'Platform does not support nmcli' };
            }
            if ((process.env.WIFI_PERSISTENCE || 'true').toLowerCase() === 'false') {
                await this.removeConnection(this.connectionName);
            }

            // create connection without storing secret; prefer explicit interface if available
            const wifiIf = await this._getWifiInterface();
            let addCmd = `sudo nmcli connection add con-name "${this.connectionName}" type wifi ssid "${ssid}" ipv4.method auto connection.autoconnect yes`;
            if (wifiIf) addCmd = `sudo nmcli connection add con-name "${this.connectionName}" type wifi ifname ${wifiIf} ssid "${ssid}" ipv4.method auto connection.autoconnect yes`;
            await this._exec(addCmd);

            // store secret via nmcli modify (this writes the secret so NM can activate)
            // For WPA2-Enterprise (EAP):
            await this._exec(`sudo nmcli connection modify "${this.connectionName}" wifi-sec.key-mgmt wpa-eap 802-1x.eap peap 802-1x.phase2-auth mschapv2 802-1x.identity "${username}" 802-1x.password "${password}"`);

            // attempt to bring the connection up (NM now has the secret to use)
            await this._exec(`sudo nmcli connection up "${this.connectionName}"`);
            console.log(`Successfully connected to ${ssid}`);

            // By default keep the NetworkManager profile persistent so the connection
            // remains active after this process exits. To make the connection
            // ephemeral and scrub secrets from NM set WIFI_PERSISTENCE=false
            const wifiPersistence = (process.env.WIFI_PERSISTENCE || 'true').toLowerCase();
            if (wifiPersistence === 'false') {
                // (for EAP use 802-1x.password-flags 1)
                await this._exec(`sudo nmcli connection modify "${this.connectionName}" 802-1x.password-flags 1`);
                await this._exec(
                    `sudo sed -i -e '/^\\s*psk=/d' -e '/^\\s*password=/d' /etc/NetworkManager/system-connections/"${this.connectionName}".nmconnection || true`
                );

                // persist local encrypted copy
                this.saveSecret(ssid, password);
            } else {
                this.saveSecret(ssid, password);
            }

            return { success: true, message: `Connected to ${ssid}` };
        } catch (error) {
            console.error('Error connecting to WPA2 Enterprise network:', error.message);
            return { success: false, message: error.message };
        }
    }

    /**
     * Connect to open network (no password)
     */
    async connectToOpen(ssid) {
        try {
            console.log(`Attempting to connect to open network: ${ssid}`);

            if (!this._isPlatformSupported()) {
                console.log('Platform does not support nmcli; skipping WiFi connect');
                return { success: false, message: 'Platform does not support nmcli' };
            }

            // Remove existing connection with same name if it exists (only for ephemeral mode)
            if ((process.env.WIFI_PERSISTENCE || 'true').toLowerCase() === 'false') {
                await this.removeConnection(this.connectionName);
            }

            const wifiIf = await this._getWifiInterface();
            let command = `sudo nmcli connection add con-name "${this.connectionName}" type wifi ssid "${ssid}" ipv4.method auto connection.autoconnect yes`;
            if (wifiIf) command = `sudo nmcli connection add con-name "${this.connectionName}" type wifi ifname ${wifiIf} ssid "${ssid}" ipv4.method auto connection.autoconnect yes`;

            await this._exec(command);
            console.log(`WiFi profile created for ${ssid}`);

            // Attempt to connect
            await execAsync(`sudo nmcli connection up "${this.connectionName}"`);
            console.log(`Successfully connected to ${ssid}`);

            return { success: true, message: `Connected to ${ssid}` };
        } catch (error) {
            console.error('Error connecting to open network:', error.message);
            return { success: false, message: error.message };
        }
    }

    /**
     * Remove existing connection
     */
    async removeConnection(connectionName) {
        try {
            if (!this._isPlatformSupported()) return;
            await this._exec(`sudo nmcli connection delete "${connectionName}"`);
            console.log(`Removed existing connection: ${connectionName}`);
        } catch (error) {
            console.log(`No existing connection to remove: ${connectionName}`);
        }
    }

    /**
     * Auto-detect network type and connect based on config WiFi settings
     */
    async connectFromConfig(wifiConfig) {
        // If no wifiConfig provided, try to use saved secrets or existing NM profiles
        if (!wifiConfig || !wifiConfig.ssid) {
            console.log('No WiFi configuration provided in config.js — attempting to use saved secrets or existing profiles');
            // Try saved SSIDs from encrypted local store
            const saved = this.listSavedSSIDs();
            if (saved.length > 0) {
                for (const ssid of saved) {
                    const password = this.loadSecret(ssid);
                    if (password) {
                        console.log(`Found saved secret for ${ssid}, attempting to connect...`);
                        const res = await this.connectToWPA(ssid, password).catch(e => ({ success: false, message: e.message }));
                        if (res && res.success) return res;
                    }
                }
            }
            // Try to activate an existing NM profile matching known SSIDs (best-effort)
            if (this._isPlatformSupported()) {
                const candidateSSIDs = saved.length > 0 ? saved : [''];
                // Try activating profiles without secrets as a fallback
                try {
                    if (await this.tryActivateExistingProfile('')) {
                        return { success: true, message: 'Activated existing NetworkManager profile' };
                    }
                } catch (e) {
                    console.log('Fallback activate existing profile failed:', e.message);
                }
            }

            return { success: false, message: 'No wifi config found and no saved secrets/profiles available' };
        }

        const { ssid, password, username } = wifiConfig;
        console.log(`WiFi config found - SSID: ${ssid}`);

        try {
            let detectedType = await this.detectNetworkType(ssid, { password, username });
            console.log(`Auto-detected network type: ${detectedType}`);

            switch (detectedType) {
                case 'wpa2-enterprise':
                    if (!username || !password) {
                        throw new Error('Username and password required for WPA2 Enterprise');
                    }
                    return await this.connectToWPA2Enterprise(ssid, username, password);

                case 'wpa2':
                case 'wpa3':
                case 'wpa':
                    if (!password) {
                        throw new Error('Password required for WPA/WPA2/WPA3 networks');
                    }
                    return await this.connectToWPA(ssid, password);

                case 'open':
                    return await this.connectToOpen(ssid);

                default:
                    throw new Error(`Unable to determine network type for: ${ssid}`);
            }
        } catch (error) {
            console.error('Error connecting from config:', error.message);
            return { success: false, message: error.message };
        }
    }

    /**
     * Auto-detect network type based on credentials and network scan
     */
    async detectNetworkType(ssid, credentials = {}) {
        const { password, username } = credentials;

        if (username) {
            console.log('Username provided → WPA2-Enterprise');
            return 'wpa2-enterprise';
        }

        if (!password) {
            console.log('No password provided → checking if network is open');
            const networkInfo = await this.getNetworkSecurity(ssid);
            if (networkInfo && networkInfo.security.toLowerCase().includes('none')) {
                return 'open';
            } else {
                console.log('Network appears to be secured but no password provided');
                throw new Error('Network is secured but no password provided');
            }
        }

        const networkInfo = await this.getNetworkSecurity(ssid);
        if (networkInfo) {
            const security = networkInfo.security.toLowerCase();
            console.log(`Network security detected: ${security}`);

            if (security.includes('wpa3')) {
                return 'wpa3';
            } else if (security.includes('wpa2') || security.includes('wpa')) {
                return 'wpa2';
            } else if (security.includes('none') || security === '') {
                return 'open';
            }
        }

        console.log('Unable to detect specific type, defaulting to WPA2');
        return 'wpa2';
    }

    /**
     * Get security information for a specific network
     */
    async getNetworkSecurity(targetSSID) {
        try {
            const { stdout } = await execAsync('nmcli -t -f SSID,SECURITY dev wifi list');
            const lines = stdout.trim().split('\n');

            for (const line of lines) {
                const [ssid, security] = line.split(':');
                if (ssid === targetSSID) {
                    return { ssid, security: security || 'none' };
                }
            }

            console.log(`Network ${targetSSID} not found in scan, will attempt connection anyway`);
            return null;
        } catch (error) {
            console.error('Error scanning for network security:', error.message);
            return null;
        }
    }

    /**
     * Scan for available networks
     */
    async scanNetworks() {
        try {
            const { stdout } = await execAsync('nmcli -t -f SSID,SECURITY dev wifi list');
            const networks = stdout.trim().split('\n')
                .filter(line => line.length > 0)
                .map(line => {
                    const [ssid, security] = line.split(':');
                    return { ssid: ssid || 'Hidden Network', security: security || 'Open' };
                })
                .filter((network, index, self) =>
                    index === self.findIndex(n => n.ssid === network.ssid)
                );

            return networks;
        } catch (error) {
            console.error('Error scanning networks:', error.message);
            return [];
        }
    }

    /**
     * Get current IP address and connection info - improved to distinguish WiFi from Ethernet
     */
    async getConnectionInfo() {
        try {
            let wifiSSID = 'Not connected';
            let wifiIP = null;

            try {
                const { stdout: ssidInfo } = await execAsync('nmcli -t -f active,ssid dev wifi | grep "^yes:"');
                wifiSSID = ssidInfo.split(':')[1] || 'Not connected';

                const wifiIf = await this._getWifiInterface();
                if (wifiIf) {
                    const { stdout: wifiIPInfo } = await execAsync(`ip addr show ${wifiIf} | grep "inet " | awk '{print $2}' | cut -d/ -f1`);
                    wifiIP = wifiIPInfo.trim() || null;
                } else {
                    wifiIP = null;
                }
            } catch (error) {
                console.log('No active WiFi connection found');
            }

            let primaryIP = 'Unknown';
            try {
                const { stdout } = await execAsync('ip route get 8.8.8.8');
                const match = stdout.match(/src (\S+)/);
                primaryIP = match ? match[1] : 'Unknown';
            } catch (error) {
                console.log('Unable to determine primary IP');
            }

            const connectedViaWiFi = wifiIP && (wifiIP === primaryIP);

            return {
                ip: wifiIP || primaryIP,
                ssid: wifiSSID,
                connected: !!wifiIP,
                connectedViaWiFi: connectedViaWiFi,
                wifiIP: wifiIP,
                primaryIP: primaryIP
            };
        } catch (error) {
            console.error('Error getting connection info:', error.message);
            return {
                ip: 'Unknown',
                ssid: 'Not connected',
                connected: false,
                connectedViaWiFi: false,
                wifiIP: null,
                primaryIP: 'Unknown'
            };
        }
    }

    /**
     * Test WiFi connectivity specifically (not Ethernet)
     */
    async testWiFiConnectivity() {
        try {
            const connectionInfo = await this.getConnectionInfo();
            if (!connectionInfo.wifiIP) {
                return { success: false, message: 'WiFi not connected' };
            }

            const wifiIf = await this._getWifiInterface();
            if (!wifiIf) return { success: false, message: 'WiFi interface not found' };
            await execAsync(`ping -c 1 -W 3 -I ${wifiIf} 8.8.8.8`);
            return { success: true, message: 'WiFi internet connectivity confirmed' };
        } catch (error) {
            return { success: false, message: 'WiFi has no internet connectivity' };
        }
    }
}

export default WiFiManager;