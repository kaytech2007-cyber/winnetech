# 📱 WTI Staff App — PWA Installation Guide

## What is a PWA?

A **Progressive Web App (PWA)** is a web application that can be installed on any device (phone, tablet, computer) and works like a native app. The WTI Staff Management System is now a full PWA with:

✅ **Installable** — Add to home screen on any device  
✅ **Offline Support** — Works without internet (cached pages)  
✅ **Responsive** — Perfect on mobile, tablet, and desktop  
✅ **Fast** — Loads instantly from cache  
✅ **App-like** — Full-screen experience, no browser UI  
✅ **Auto-updates** — Gets new features automatically  

---

## 📲 How to Install on Different Devices

### **Android (Chrome/Edge)**

1. Open the website in Chrome or Edge browser
2. Look for the **"Install WTI Staff App"** banner at the bottom
3. Tap **"Install"** button
4. Or tap the **⋮ menu** → **"Add to Home screen"** or **"Install app"**
5. Confirm installation
6. The app icon will appear on your home screen

**Alternative:**
- Tap the **⋮ menu** in Chrome
- Select **"Install app"** or **"Add to Home screen"**
- Name it "WTI Staff" and tap "Add"

### **iPhone/iPad (Safari)**

1. Open the website in **Safari** browser (must be Safari, not Chrome)
2. Tap the **Share button** (square with arrow pointing up) at the bottom
3. Scroll down and tap **"Add to Home Screen"**
4. Name it "WTI Staff" and tap **"Add"**
5. The app icon will appear on your home screen

**Note:** iOS doesn't show the install banner automatically — you must use the Share button method.

### **Windows (Chrome/Edge)**

1. Open the website in Chrome or Edge
2. Look for the **install icon** (⊕ or computer icon) in the address bar
3. Click it and select **"Install"**
4. Or click the **⋮ menu** → **"Install WTI Staff"**
5. The app will open in its own window
6. Find it in your Start Menu

### **Mac (Chrome/Safari)**

**Chrome:**
1. Open the website in Chrome
2. Click the **⋮ menu** → **"Install WTI Staff"**
3. Or look for the install icon in the address bar
4. The app opens in its own window

**Safari:**
1. Open the website in Safari
2. Click **File** → **"Add to Dock"**
3. The app will be added to your Dock

---

## 🎯 Features When Installed

### **Works Offline**
- View cached pages even without internet
- Attendance history, timetable, and notifications available offline
- Automatic sync when back online

### **Full-Screen Experience**
- No browser address bar or tabs
- Looks and feels like a native app
- Immersive interface

### **Fast Loading**
- Instant startup from cache
- No waiting for page loads
- Smooth animations

### **Push Notifications** (Coming Soon)
- Get class reminders even when app is closed
- Attendance alerts
- Admin announcements

### **Auto-Updates**
- New features download automatically
- You'll see an "Update Available" banner
- Click "Update" to get the latest version

---

## 🔧 Technical Details

### **Service Worker**
- Caches static assets (CSS, JS, images)
- Provides offline fallback pages
- Network-first strategy for dynamic content
- Cache-first for static resources

### **Manifest**
- App name: "Winneba Technical Institute"
- Short name: "WTI Staff"
- Theme color: Gold (#f5a623)
- Background: Dark Blue (#0a1628)
- Display: Standalone (full-screen)
- Icons: 8 sizes (72px to 512px)

### **Offline Support**
- Welcome page cached
- Login pages cached
- Static assets (CSS, JS, fonts) cached
- Offline fallback page shown when no connection
- API calls fail gracefully with empty responses

### **Mobile Optimizations**
- Safe area insets for iPhone notch/home bar
- Touch-optimized buttons (44px minimum)
- No zoom on input focus (16px font size)
- Smooth scrolling with momentum
- Bottom navigation on mobile
- Responsive breakpoints: 576px, 768px, 992px

---

## 📱 Mobile Features

### **Bottom Navigation (Mobile)**
**Facilitator Portal:**
- Home (Dashboard)
- Timetable
- **QR Scanner** (center, elevated button)
- Chat
- Logout

**Admin Portal:**
- Dashboard
- Facilitators
- **QR Code** (center, elevated button)
- Attendance
- Chat

### **Responsive Design**
- **Mobile (< 576px):** Single column, bottom nav, compact cards
- **Tablet (577-992px):** Two columns, sidebar toggle
- **Desktop (> 992px):** Full sidebar, multi-column layout

### **Touch Gestures**
- Tap to select
- Swipe to scroll
- Pull to refresh (coming soon)
- Long-press disabled on UI elements

---

## 🚀 Performance

### **Load Times**
- **First visit:** ~2-3 seconds (downloads assets)
- **Return visits:** < 1 second (from cache)
- **Offline:** Instant (cached pages)

### **Cache Strategy**
- **Static assets:** Cache-first (instant load)
- **HTML pages:** Network-first with cache fallback
- **API calls:** Network-only (no stale data)
- **Images:** Cache-first with network update

### **Storage**
- **Cache size:** ~5-10 MB (static assets)
- **IndexedDB:** Not used (future feature)
- **LocalStorage:** PWA dismissal state only

---

## 🔒 Security

### **HTTPS Required**
- PWA only works on HTTPS (secure connection)
- Service workers require secure context
- Development: localhost is allowed

### **Permissions**
- **Camera:** Required for QR scanner
- **Notifications:** Optional (for reminders)
- **Storage:** Automatic (for cache)

---

## 🐛 Troubleshooting

### **Install Banner Not Showing**
- Make sure you're on HTTPS (not HTTP)
- Check if already installed (look for app icon)
- Try refreshing the page
- Clear browser cache and reload
- On iOS: Use Safari Share → Add to Home Screen

### **App Not Working Offline**
- Visit the app online first (to cache assets)
- Check if service worker is registered (DevTools → Application → Service Workers)
- Clear cache and revisit to re-cache

### **Update Not Applying**
- Close all app windows/tabs
- Reopen the app
- Click "Update" when banner appears
- Or uninstall and reinstall

### **QR Scanner Not Working**
- Grant camera permission when prompted
- Use HTTPS (camera requires secure context)
- Try a different browser (Chrome recommended)
- Check if camera is being used by another app

### **Uninstall the App**

**Android:**
- Long-press app icon → **"Uninstall"** or **"App info"** → **"Uninstall"**

**iOS:**
- Long-press app icon → **"Remove App"** → **"Delete App"**

**Windows:**
- Right-click app icon → **"Uninstall"**
- Or Settings → Apps → WTI Staff → Uninstall

**Mac:**
- Drag app from Dock to Trash
- Or Applications folder → Move to Trash

---

## 📊 Browser Support

| Browser | Install | Offline | Notifications |
|---------|---------|---------|---------------|
| Chrome (Android) | ✅ | ✅ | ✅ |
| Chrome (Desktop) | ✅ | ✅ | ✅ |
| Edge (All) | ✅ | ✅ | ✅ |
| Safari (iOS) | ✅ | ✅ | ❌ |
| Safari (Mac) | ✅ | ✅ | ❌ |
| Firefox | ⚠️ | ✅ | ⚠️ |
| Samsung Internet | ✅ | ✅ | ✅ |

✅ Full support | ⚠️ Partial support | ❌ Not supported

---

## 🎓 For Administrators

### **Deploying the PWA**

1. **Ensure HTTPS:** PWA requires secure connection
2. **Update manifest.json:** Change `start_url` if needed
3. **Generate icons:** Run `python generate_icons.py` if needed
4. **Test service worker:** Open DevTools → Application → Service Workers
5. **Test offline:** DevTools → Network → Offline checkbox

### **Updating the PWA**

1. Make code changes
2. Update `CACHE_NAME` in `static/js/sw.js` (e.g., `wti-staff-v2`)
3. Deploy changes
4. Users will see "Update Available" banner
5. They click "Update" to get new version

### **Monitoring**

- Check service worker status in browser DevTools
- Monitor cache size (Application → Cache Storage)
- Test offline functionality regularly
- Verify manifest.json is accessible at `/static/manifest.json`

---

## 📞 Support

For issues or questions:
1. Check this guide first
2. Try troubleshooting steps above
3. Contact IT support at WTI
4. Check browser console for errors (F12 → Console)

---

**Winneba Technical Institute**  
*Transforming and Empowering the Youth Through Vocational Skills*

🌐 **Website:** http://127.0.0.1:8000/  
📱 **Install:** Look for the install banner or use browser menu  
🔄 **Version:** 1.0.0 (May 2026)
