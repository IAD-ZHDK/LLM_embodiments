#pragma once

// Template for WifiSecrets.h — copy this file to WifiSecrets.h and fill in your own values.
// WifiSecrets.h is gitignored so real credentials never get committed.
namespace DeviceConfig
{
    struct WifiCredential
    {
        const char *ssid;
        const char *password;
    };

    inline const WifiCredential kWifiCredentials[] = {
        {"YourPrimaryNetwork", "YourPrimaryPassword"},
        {"YourBackupNetwork", "YourBackupPassword"},
    };
    inline const size_t kWifiCredentialCount = sizeof(kWifiCredentials) / sizeof(kWifiCredentials[0]);
    // Prefer the backend machine's Bonjour hostname (e.g. "YourMac.local") over its DHCP IP, so
    // this keeps working after the backend's IP changes (e.g. on restart) without reconfiguring
    // this device. A plain IP also still works if you'd rather use that.
    inline const char *kFallbackBackendHost = "192.168.4.237";
    inline const uint16_t kFallbackBackendPort = 3000;
    inline const char *kFallbackBackendPath = "/device";
} // namespace DeviceConfig
