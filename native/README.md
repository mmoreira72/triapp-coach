# Tri Squad native app (Capacitor)

The iOS app is a native shell around tri.laloop.ai: it opens the live site (same login, same pages) and adds what a web page
cannot do. Android can be added later from the same project (`npx cap add android`).

| Phase | What | Status |
|---|---|---|
| 1 | Shell: app icon, offline page, navigation locked to tri.laloop.ai | done |
| 2 | Push notifications (APNs): session note ready, skill alerts (`push.py`, `notify.py`) | live (production key K76LNTK436, sandbox key 9838SJK697) |
| 3 | HealthKit: sleep, resting HR, HRV sent to the box (`HealthPlugin.swift`, `health.py`, recovery charts) | built, to test on the iPhone |
| 4 | Home-screen widget: today's form and the last session (`TriWidget/`, `WidgetBridgePlugin.swift`) | built, to test on the iPhone |
| 5 | On-device LLM (Apple Foundation Models, iOS 26+) as the assistant's fallback (`LocalLLMPlugin.swift`) | built, to test on the iPhone |

## One-time setup

1. Install Xcode from the App Store, then: `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`
2. In Xcode > Settings > Accounts, sign in with the Apple ID of the developer account.
3. `cd native && npm install && ./vendor-capacitor.sh && npx cap sync ios && npx cap open ios`
   (`vendor-capacitor.sh`: Swift Package Manager hangs downloading Capacitor's binaries on this network, so they are fetched
   with curl, checked against the package's checksums and served from a local copy)
4. In Xcode, targets App and TriWidget > Signing & Capabilities: pick the team. Push, Background Modes, HealthKit and the
   App Group `group.ai.laloop.trisquad` are already in the entitlements; Xcode registers the ids on first signing.
5. Run on the iPhone (cable or same Wi-Fi).

## Push (APNs)

In developer.apple.com > Certificates, IDs & Profiles > Keys, create a key with "Apple Push Notifications service" and
download `AuthKey_<KEYID>.p8` (it can be downloaded only once). On the box:

```sh
mkdir -p ~/triapp-out/secrets && chmod 700 ~/triapp-out/secrets
# copy the .p8 there (scp), then chmod 600 it, and add to ~/triapp/.env:
TRIAPP_APNS_KEY_PATH=/out/secrets/AuthKey_<KEYID>.p8
TRIAPP_APNS_KEY_ID=<KEYID>
TRIAPP_APNS_TEAM_ID=<TEAMID>
TRIAPP_APNS_ENV=production       # tried first; the other environment is tried when Apple says the token belongs there
# when the main key is topic-specific to Production, add a second key for builds run from Xcode:
TRIAPP_APNS_SANDBOX_KEY_PATH=/out/secrets/AuthKey_<SANDBOXKEYID>.p8
TRIAPP_APNS_SANDBOX_KEY_ID=<SANDBOXKEYID>
```

Then `docker compose up -d`. Test from the app (logged in): `POST /api/push/test` sends a notification to your own devices.

## TestFlight

From the command line (VPN off): `xcodebuild -project ios/App/App.xcodeproj -scheme App -configuration Release -destination 'generic/platform=iOS' -archivePath /tmp/TriSquad.xcarchive -allowProvisioningUpdates archive` then `xcodebuild -exportArchive -archivePath /tmp/TriSquad.xcarchive -exportOptionsPlist ExportOptions.plist -exportPath /tmp/TriSquad-export -allowProvisioningUpdates` (uploads, build number set automatically; `testFlightInternalTestingOnly` must stay false or the build cannot go to external groups or the public link). Or Product > Archive in Xcode, upload, then add the squad in App Store Connect > TestFlight. Internal testers (people added
to the App Store Connect team, up to 100) skip Apple's beta review. Switch `aps-environment` in `App.entitlements` to
`production` and `TRIAPP_APNS_ENV=production` for TestFlight builds.
