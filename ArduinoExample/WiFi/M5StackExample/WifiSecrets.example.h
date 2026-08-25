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
    inline const char *kFallbackBackendHost = "192.168.1.10";
    inline const uint16_t kFallbackBackendPort = 3000;
    inline const char *kFallbackBackendPath = "/device";
} // namespace DeviceConfig
