# Morphe AutoBuilds

## 🔄 Update Schedule
- Automatic updates daily at 9:17 AM Africa/Cairo time
- Manual updates available via workflow dispatch

## ⚙️ Configuration
Edit [config/morphe-config.json](config/morphe-config.json) to add apps, sources, build entries, or patch selections.
Generated runtime files are synchronized by the **Sync Configuration** workflow.

## 🛠️ Manage Configuration

Use **Actions → Manage Configuration → Run workflow** to change the repository configuration without editing JSON files manually. The workflow has four operations:

### 1. Add Application

Use this when adding a new app for the first time. Select **add-app**, then fill these fields:

| Field | What to enter | Example |
|---|---|---|
| **Application** | Keep **__NEW_APP__** | <code>__NEW_APP__</code> |
| **New application ID** | Lowercase app ID; letters, numbers, and hyphens only | <code>truecaller</code> |
| **Display name** | Human-readable app name | <code>Truecaller</code> |
| **Package name** | Android package name | <code>com.truecaller</code> |
| **Provider** | Store/source used to obtain the original app | <code>apkmirror</code> |
| **Provider reference** | For APKMirror use <code>org/name</code>; for other providers use the provider's app name | <code>truecaller/truecaller</code> |
| **Provider type** | <code>APK</code> for a standalone APK, <code>BUNDLE</code> for a native split bundle such as APKM | <code>BUNDLE</code> |
| **DPI** | APKMirror DPI when required; normally <code>nodpi</code> | <code>nodpi</code> |
| **Architecture** | Target build architecture | <code>arm64-v8a</code> |
| **Patch Source URL** | GitHub/GitLab patch repository URL, or a Morphe add-source link | <code>https://morphe.software/add-source?gitlab=Paresh-Maheshwari/paresh-patches</code> |

The patch source is read during registration and the source's package-specific Morphe default patch selection is materialized into <code>patches/&lt;app&gt;-&lt;source&gt;.txt</code>. You do not need to manually enter individual patches when adding the app.

**Example — Truecaller:** set Application to <code>__NEW_APP__</code>, New application ID to <code>truecaller</code>, Display name to <code>Truecaller</code>, Package name to <code>com.truecaller</code>, choose the correct original-app provider, set the correct provider reference and artifact type, choose <code>arm64-v8a</code>, and enter the Truecaller patch-source URL.

### 2. Edit Patch Selection

Use this to change the patches used for an app that is already configured. The patch choices are read from the committed <code>patches/</code> directory.

| Field | What to enter |
|---|---|
| **Application** | Select the existing app you want to change |
| **Source** | Select the patch source used by that app; use <code>__AUTO__</code> when the app has only one build/source entry |
| **Patch Action** | <code>add</code> = force-enable, <code>exclude</code> = disable/exception, <code>reset</code> = remove the override and return to the source default |
| **Patch** | Select the exact patch from the dropdown. Each item is shown as <code>app / source — patch</code> |

The workflow verifies that the selected patch belongs to the selected application/source before changing the configuration.

### 3. Delete Application

Use this to completely remove an application from the build configuration.

| Field | What to enter |
|---|---|
| **Application** | Select the app to delete |
| **Confirm delete** | Must be enabled (<code>true</code>) |

Deletion removes the app and all of its build entries. A shared patch source is not deleted automatically because another app may still use it. Generated runtime files are synchronized after the change.

### 4. Enable / Disable Application

Use **set-app-status** to control whether an application's configured build entries participate in automatic builds.

| Field | What to enter |
|---|---|
| **Application** | Select the existing app |
| **Status** | <code>enabled</code> or <code>disabled</code> |

For an app with multiple patch sources/build entries, the status is applied to all of that app's build entries.

### Important Notes

- **Manage Configuration changes configuration only. It does not build APKs.** After a configuration change is committed, the normal **Auto Build and Release Morphe** workflow handles building and publishing according to the existing build logic.
- Patch selections are maintained in <code>patches/</code>. The GUI dropdown is generated from those committed files, so only patches already represented there can be selected in **Edit Patch Selection**.
- When no patch override is needed, keep the app/source at the Morphe default selection. Use **reset** to remove an explicit override.

## YouTube — App v21.16.256 — Patch v1.45.0
Patch source: morphe — v1.45.0 ([v1.45.0](https://github.com/MorpheApp/morphe-patches))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 90/96 applied</summary>

- ❌ Clone app
- ❌ Change installer source
- ❌ Override certificate pinning
- ❌ Spoof signature
- ✅ Disable Play Store updates
- ✅ Hide ads
- ✅ Channel search
- ✅ Copy video link
- ✅ Remove viewer discretion dialog
- ✅ Disable double tap actions
- ✅ Double tap to seek
- ✅ Downloads
- ✅ Disable haptic feedback
- ✅ Loop video
- ✅ Picture-in-picture button
- ✅ Play all
- ✅ Reload video
- ✅ Save to Watch later
- ✅ Seekbar
- ✅ Swipe controls
- ❌ Custom branding
- ✅ Change header
- ✅ Hide video action buttons
- ✅ Navigation bar
- ✅ Hide player overlay buttons
- ✅ Captions
- ✅ Disable layout updates
- ✅ Disable auto feed refresh
- ✅ Add to queue
- ✅ Change form factor
- ✅ Ambient mode
- ✅ Hide autoplay preview
- ✅ Hide end screen cards
- ✅ Hide end screen suggested video
- ✅ Hide layout components
- ✅ Hide info cards
- ✅ Hide player flyout menu components
- ✅ Disable player popup panels
- ✅ Hide related video overlay
- ✅ Hide related videos
- ✅ Disable rolling number animations
- ✅ Settings menu filter
- ✅ Hide Shorts components
- ✅ Disable sign in to TV popup
- ✅ Hide timestamp
- ✅ Open channel of live avatar
- ✅ Miniplayer
- ✅ Override YouTube Music buttons
- ✅ Restore original titles
- ✅ Playback in feeds
- ✅ Mute button
- ✅ Disable fullscreen gestures
- ✅ Exit fullscreen mode
- ✅ Force fullscreen landscape
- ✅ Fullscreen video scale
- ✅ Open videos fullscreen
- ✅ Player icon style
- ✅ Custom player overlay opacity
- ✅ Disable playlist autoplay
- ✅ Return YouTube Dislike
- ✅ Disable scrolling speed limit
- ✅ Open system share sheet
- ✅ Shorts autoplay
- ✅ Shorts icon style
- ✅ Disable Shorts resuming on startup
- ✅ Open Shorts in regular player
- ✅ SponsorBlock
- ✅ Change start page
- ✅ Hide status bar
- ✅ Theme
- ✅ Alternative thumbnails
- ✅ Bypass image region restrictions
- ✅ Wide search bar
- ✅ Remove background playback restrictions
- ✅ Enable debugging
- ✅ Check watch history domain name resolution
- ✅ GmsCore support
- ✅ Bypass link redirects
- ✅ Open links externally
- ✅ Media notification controls
- ✅ PoToken provider
- ✅ Sanitize sharing links
- ❌ Network proxy
- ✅ Disable QUIC protocol
- ✅ App refresh rate
- ✅ Spoof video streams
- ✅ Spoof app version
- ✅ Spoof device dimensions
- ✅ Disable DRC audio
- ✅ Force original audio
- ✅ Playback buffer
- ✅ Disable video codecs
- ✅ Remember live stream playback position
- ✅ Video quality
- ✅ Playback speed
- ✅ Voice over translation

</details>

## Reddit — App v2026.24.0 — Patch v1.45.0
Patch source: morphe — v1.45.0 ([v1.45.0](https://github.com/MorpheApp/morphe-patches))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 17/24 applied</summary>

- ❌ Clone app
- ❌ Change installer source
- ❌ Override certificate pinning
- ❌ Spoof signature
- ❌ Disable Play Store updates
- ✅ Hide ads
- ✅ Custom font
- ✅ Force system font
- ✅ Hide Ask button
- ❌ Custom branding name for Reddit
- ✅ Hide communities shelf
- ✅ Disable modern home
- ✅ Hide navigation buttons
- ✅ Disable screenshot popup
- ❌ Hide Reddit search
- ✅ Hide sidebar components
- ✅ Remove subreddit dialog
- ✅ Hide Trending shelves
- ✅ Show view count
- ✅ App icon
- ✅ Start as guest
- ✅ Open links directly
- ✅ Open links externally
- ✅ Sanitize sharing links

</details>

## X — App v12.29.1-prod.01 — Patch v3.48.0
Patch source: piko-newx — v3.48.0 ([v3.48.0](https://github.com/crimera/piko-newx))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 42/46 applied</summary>

- ✅ NewX: Remove ads
- ✅ NewX: Disable blur effects
- ❌ NewX: Restore Twitter branding
- ❌ NewX: Browse tweet object
- ✅ NewX: Open canonical URLs
- ✅ NewX: Crash logs
- ✅ NewX: Custom font
- ✅ NewX: Custom sharing domain
- ✅ NewX: Customize drawer items
- ✅ NewX: Theme
- ✅ NewX: Feature switch overrides
- ✅ NewX: Classic inline action spacing
- ✅ NewX: Customize inline actions
- ✅ NewX: Inline download button
- ✅ NewX: Redirect downloads to chosen folder
- ✅ NewX: Force highest video/audio quality
- ✅ NewX: Customize media menu items
- ✅ NewX: Set default media tab
- ✅ NewX: Gallery profile Photos tab
- ✅ NewX: Customize navigation bar
- ✅ NewX: Hide post reply bar
- ✅ NewX: Customize post menu items
- ✅ NewX: Set default profile post sorting
- ✅ NewX: Set default reply sorting
- ❌ NewX: Server error logging
- ✅ NewX: Share post as image
- ✅ NewX: Disable video player scrolling
- ✅ NewX: Hide premium upsell
- ✅ NewX: Unlock color customization
- ✅ NewX: Unlock downloads
- ✅ NewX: Customize timeline tabs
- ✅ NewX: Disable automatic timeline refresh
- ✅ NewX: Filter For You by topic
- ✅ NewX: Hide AI-generated posts
- ✅ NewX: Hide Discover more
- ✅ NewX: Hide compose button
- ✅ NewX: Hide new posts pill
- ❌ NewX: Hide post dividers
- ✅ NewX: Hide Spaces bar
- ✅ NewX: Hide timeline tabs bar
- ✅ NewX: Hide who to follow
- ✅ NewX: Restore timeline position
- ✅ NewX: Show poll results
- ✅ NewX: Show sensitive media
- ✅ NewX: Hide posts by verified account type
- ✅ NewX: Filter posts by keyword

</details>

## Gboard — App v18.0.3.954559732-release-arm64-v8a — Patch v3.11.0
Patch source: jasonwu1994 — v3.11.0 ([v3.11.0](https://github.com/jasonwu1994/Gboard-patches))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 42/42 applied</summary>

- ✅ English QWERTY Up-Flick Uppercase
- ✅ Long-Press Editing Shortcuts
- ✅ G Logo on Spacebar
- ✅ Incognito Mode Toggle
- ✅ Toolbar Editing Buttons
- ✅ Floating Web Search
- ✅ Simple Calculator
- ✅ Advanced Voice Typing
- ✅ Use Bluetooth Microphone
- ✅ Change emoji size
- ✅ Enable cursor trackpad mode
- ✅ Access Points menu style
- ✅ Enable split keyboard
- ✅ Enable accessibility layout
- ✅ Rounded Keyboard Panel
- ✅ Top Toolbar Item Count
- ✅ Close Proactive Suggestions
- ✅ Hyperspeed Typing Animation
- ✅ Custom Theme
- ✅ Quick Insert
- ✅ Zhuyin Quick Traditional/Simplified Toggle
- ✅ Custom Symbols
- ✅ Swipeable Custom Top Row
- ✅ Developer options
- ✅ Backup & Restore
- ✅ Emojis, stickers & GIFs Tab Order
- ✅ Clipboard Enhancements
- ✅ Clipboard Custom Character Limit
- ✅ Web Clipboard
- ✅ FTP Server
- ✅ Enable Inline Autofill Suggestions
- ✅ Grammar Checker
- ✅ Inline Suggestions
- ✅ Key Shape Selection
- ✅ AI Writing Tools
- ✅ Enable OCR / Scan Text
- ✅ Settings Homepage Override
- ✅ Latin Globe Key Ignore Interval
- ✅ Zhuyin Bottom Row Key Sizes
- ✅ Package Rename
- ✅ Add Gboard Signature Bypass
- ✅ Zhuyin Slide Input

</details>

## vpnify — App v2.3.0 — Patch v1.38.0
Patch source: hxreborn — v1.38.0 ([v1.38.0](https://github.com/hxreborn/morphe-patches))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 2/3 applied</summary>

- ❌ Override certificate pinning
- ✅ Unlock premium
- ✅ Disable rating prompt

</details>

## TikTok — App v47.1.4 — Patch v0.66.0
Patch source: hushfeed — v0.66.0 ([v0.66.0](https://github.com/SysAdminDoc/hushfeed))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 39/102 applied</summary>

- ❌ Hide the risk control CAPTCHA
- ✅ Hide CAPTCHA popups
- ✅ Feed filter
- ❌ Allow screenshots and Circle to Search
- ✅ Disable screen capture detection
- ❌ Show author region
- ❌ Keep playing in the background
- ❌ Block author button
- ❌ Subtitle tools
- ✅ Remember clear display
- ❌ Confirm feed interactions
- ❌ Advanced downloads
- ✅ Downloads
- ❌ Allow Duet and Stitch
- ✅ Keep the Favorites tab
- ✅ Hide feed save button
- ✅ Hide feed follow button
- ❌ Mute feed videos
- ✅ Hide feed LIVE button
- ✅ Hide feed search button
- ❌ Double-tap controls
- ❌ Long-press controls
- ❌ Swipe-left controls
- ❌ Ghost mode
- ❌ Automatic video advance
- ✅ Stay on the video in full screen
- ✅ Stop video looping
- ❌ Not interested button
- ✅ Custom offline videos limit
- ✅ Always show publish date
- ❌ Playback quality
- ✅ Disable the long press quick share
- ✅ Disable the long press repost
- ✅ Hide comment typing suggestions
- ✅ Resume videos after scrolling
- ✅ Use non-personalized search
- ✅ Show LIVE search
- ❌ Hide search suggestions
- ✅ Show the progress bar
- ✅ Show the progress bar thumbnail
- ❌ Hide already seen videos
- ❌ Skip content warnings
- ❌ Share sheet tools
- ✅ Hold-and-slide 2x lock
- ✅ Playback speed
- ❌ Use system font
- ❌ Fit the video to the screen
- ❌ Hide video overlays
- ❌ Change app name
- ✅ Copy comments without username
- ✅ Comment publish diagnostics
- ✅ Comment send fix
- ❌ Comment sort controls
- ❌ Comment tools
- ❌ Hide comment popup ads
- ✅ Open external links directly
- ✅ Feature Gate Lab
- ❌ Feature Gate Recorder
- ❌ Foldable split comment view
- ✅ Follow diagnostics
- ❌ Keep a streak going
- ❌ Expand activity list
- ❌ Hide inbox stories
- ❌ Hide suggested accounts
- ❌ Hide inbox items
- ❌ Notification controls
- ✅ Disable login requirement
- ✅ Fix Google login
- ✅ Feed tab navigation
- ❌ Limit background traffic
- ❌ Drop the animated image cache
- ❌ Skip update checks
- ❌ Skip the splash ad
- ❌ Remove creation tools
- ❌ Remove content credential and card scanner assets
- ❌ Remove unused language packs
- ❌ Remove LIVE extras
- ❌ Block P2P video relay
- ❌ Keep the screen's refresh rate
- ✅ Repost diagnostics
- ❌ Diagnostic tools
- ✅ Settings
- ✅ Sanitize sharing links
- ❌ Hide the launcher shortcuts
- ❌ Region spoof
- ✅ SIM spoof
- ❌ Disable telemetry
- ❌ AMOLED dark theme
- ✅ Translate comments
- ❌ Hide Play Store update offer
- ❌ Enable voice comments
- ❌ Stop on-device AI profiling
- ❌ In-app browser privacy guard
- ❌ Camera and microphone indicator
- ❌ Block contact list access
- ❌ Device privacy guard
- ❌ Block installed app scanning
- ❌ Location access governor
- ❌ Network request report
- ❌ Resource and battery governor
- ❌ Look like the store app
- ✅ Hide floating promotions

</details>

## TikTok (Metra) — App v46.2.3 — Patch v0.8.0
Patch source: metra — v0.8.0 ([v0.8.0](https://github.com/icysymmetra/tiktok-patches-for-morphe))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 41/42 applied</summary>

- ✅ Hide CAPTCHA popups
- ✅ Feed filter
- ✅ Hide AI content
- ✅ Hide FYP unpersonalized slop videos
- ✅ Disable screen capture detection
- ✅ Force show Auto scroll
- ✅ Remember clear display
- ✅ Downloads
- ✅ Hide feed save button
- ✅ Hide feed follow button
- ✅ Hide feed LIVE button
- ✅ Hide feed search button
- ✅ Stop video looping
- ✅ Custom offline videos limit
- ✅ Always show publish date
- ✅ Disable long-press quick share
- ✅ Disable long-press repost
- ✅ Hide quick comment reactions
- ✅ Resume videos after scrolling
- ✅ Enable non-personalized search
- ✅ Enable Live search
- ✅ Show seekbar
- ✅ Show seekbar thumbnail
- ✅ Hold-and-slide 2x lock
- ✅ Playback speed
- ✅ Copy comments without username
- ✅ Comment sort controls
- ✅ Open external links directly
- ✅ Feature Gate Lab
- ✅ Foldable split comment view
- ✅ Disable login requirement
- ✅ Fix Google login
- ✅ Feed tab navigation
- ❌ Diagnostic tools
- ✅ Settings
- ✅ Sanitize sharing links
- ✅ Share sheet modification
- ✅ Region spoof
- ✅ Hide suggested accounts
- ✅ Translate comments
- ✅ Enable voice comments
- ✅ Hide floating promotions

</details>

## CamScanner — App v7.20.0.2606230000 — Patch v1.46.0
Patch source: hoodles — v1.46.0 ([v1.46.0](https://github.com/hoo-dles/morphe-patches))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 2/7 applied</summary>

- ❌ Hide app icon
- ❌ Enable debug
- ❌ Change package name
- ❌ MicroG integration
- ❌ Disable Pairip license check
- ✅ Disable telemetry
- ✅ Enable Premium

</details>

## Messenger — App v573.0.0.44.88 — Patch v1.4.4
Patch source: devanced — v1.4.4 ([v1.4.4](https://github.com/RookieEnough/De-Vanced))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 7/9 applied</summary>

- ❌ Clone app
- ✅ Hide inbox ads
- ✅ Hide inbox stories and notes tray
- ✅ Hide inbox subtabs
- ✅ Disable typing indicator
- ✅ Hide Facebook buttons
- ✅ Open links externally
- ✅ Remove Meta AI
- ❌ Spoof package version

</details>

## Messenger — App v580.0.0.49.91 — Patch v0.8.0
Patch source: hushmessenger — v0.8.0 ([v0.8.0](https://github.com/SysAdminDoc/HushMessenger))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 22/31 applied</summary>

- ❌ Install beside Meta apps
- ❌ Restore screens on re-signed builds
- ❌ Material You theme
- ❌ Hide inbox ads
- ✅ Hide People You May Know
- ✅ Hide friend request cards
- ✅ Hide growth prompts
- ✅ Hide inbox promotions
- ✅ Hide stories and notes
- ✅ Hide inbox tabs
- ✅ Hide Facebook shortcuts
- ✅ Hide Meta AI
- ✅ Hide Chat Moments
- ✅ Hide Reels badge
- ✅ Hide AI sticker tools
- ✅ Hide avatar stickers
- ✅ Hide chat promotions
- ✅ Hide business reply suggestions
- ✅ Hide business typing suggestions
- ✅ Hide event prompts
- ✅ Hide typing indicator
- ❌ Open web links externally
- ❌ Allow chat bubbles
- ✅ Use system emoji
- ✅ Send photos at original quality
- ❌ Allow screenshots
- ❌ Hide read receipts
- ❌ Keep unsent messages
- ✅ View stories anonymously
- ✅ Save any story
- ✅ Open settings from menu

</details>

## Facebook — App v580.0.0.51.74 — Patch v0.6.0
Patch source: hushfacebook — v0.6.0 ([v0.6.0](https://github.com/SysAdminDoc/Hushfacebook))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 47/59 applied</summary>

- ✅ Hide affiliate product links
- ✅ Disable Audience Network
- ✅ Block background ad prefetch
- ✅ Hide sponsored Marketplace listings
- ✅ Hide sponsored posts
- ✅ Hide sponsored profile posts
- ✅ Hide sponsored reels
- ✅ Hide sponsored search results
- ✅ Hide sponsored stories
- ✅ Block ad telemetry
- ✅ Hide the Get Messenger card
- ✅ Open Messenger from the top bar
- ✅ Install beside Meta's apps
- ❌ Default comment order
- ❌ Tag suggestions only after @
- ✅ Download any reel
- ✅ Download any story
- ✅ Download any video
- ❌ Use the phone's emoji
- ✅ Hide AI-detected posts
- ✅ Hide the Feeds header
- ✅ Hide Meta AI questions under posts
- ✅ Keep post dates
- ✅ Hide post prompts
- ✅ Hide Reels in the feed
- ✅ Block background-return feed refresh
- ✅ Hide Stories tray
- ✅ Hide suggested and promoted posts
- ✅ Hide posts by words
- ❌ Use the system font
- ❌ AMOLED black theme
- ❌ Material You theme
- ❌ Default playback quality
- ✅ Keep the reel speed
- ✅ Resume long videos
- ❌ Tap to play
- ✅ Hide Menu promotions
- ✅ Hushfacebook in the Menu
- ✅ Open links in external browser
- ✅ Restore screens on re-signed builds
- ✅ Hushfacebook settings
- ✅ Sanitize sharing links
- ✅ Start on x86 devices
- ✅ Tab bar at the bottom
- ✅ Marketplace only
- ✅ Hide the Reels tab
- ✅ Hide the Reels tab dot
- ❌ Open on a chosen tab
- ✅ Block promotional notifications
- ✅ Clean up Reels
- ✅ Turn off double tap to like
- ✅ Hold a reel for 2x
- ✅ Hide reel interest prompts
- ❌ Don't send reel watch history
- ✅ Hide Meta AI in search
- ❌ Stop Story auto-advance
- ❌ View stories anonymously
- ✅ Hide suggested stories
- ✅ Stop update prompts

</details>

## Excel — App v16.0.20228.20090 — Patch v1.22.0
Patch source: rushi-excel — v1.22.0 ([v1.22.0](https://github.com/rushiranpise/morphe-patches))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 1/8 applied</summary>

- ✅ Unlock Excel
- ❌ Disable PairIP license check
- ❌ Provide Original app certificate
- ❌ Spoof Widevine / DRM level
- ❌ Fix Firebase after re-signing
- ❌ GmsCore support (MicroG)
- ❌ Spoof install source
- ❌ Spoof app signature

</details>

## Truecaller — App v— — Patch v1.45.0
Patch source: morphe — v1.45.0 ([v1.45.0](https://github.com/MorpheApp/morphe-patches))
Architecture: arm64-v8a

<details>
<summary>🩹 Patches — 0/5 applied</summary>

- ❌ Clone app
- ❌ Change installer source
- ❌ Override certificate pinning
- ❌ Spoof signature
- ❌ Disable Play Store updates

</details>
