#!/bin/bash
# Generate Featherweight app icon as .icns from a Swift-rendered PNG
set -e

ICONSET="Featherweight.iconset"
mkdir -p "$ICONSET"

# Generate the base 1024px icon using a Swift one-liner
swift - <<'SWIFT'
import AppKit

let size = NSSize(width: 1024, height: 1024)
let image = NSImage(size: size)

image.lockFocus()

// Background: rounded rect with gradient
let bg = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 200, yRadius: 200)
let gradient = NSGradient(colors: [
    NSColor(red: 0.15, green: 0.15, blue: 0.18, alpha: 1),
    NSColor(red: 0.10, green: 0.10, blue: 0.13, alpha: 1)
])
gradient?.draw(in: bg, angle: -45)
bg.lineWidth = 6
NSColor(white: 0.25, alpha: 1).setStroke()
bg.stroke()

// Broom icon (SF Symbol-style, drawn manually)
let broomColor = NSColor(red: 0.0, green: 0.48, blue: 1.0, alpha: 1) // #007AFF
let accentColor = NSColor(red: 0.6, green: 0.2, blue: 1.0, alpha: 1) // #9933FF

// Broom handle
let handle = NSBezierPath()
handle.lineWidth = 32
handle.lineCapStyle = .round
handle.move(to: NSPoint(x: 640, y: 700))
handle.line(to: NSPoint(x: 480, y: 380))
broomColor.setStroke()
handle.stroke()

// Broom head (brush part)
let brush = NSBezierPath()
brush.lineWidth = 28
brush.lineCapStyle = .round
brush.move(to: NSPoint(x: 480, y: 380))
brush.line(to: NSPoint(x: 420, y: 260))
brush.line(to: NSPoint(x: 380, y: 300))
brush.line(to: NSPoint(x: 340, y: 240))
brush.line(to: NSPoint(x: 300, y: 290))
brush.line(to: NSPoint(x: 360, y: 340))
brush.line(to: NSPoint(x: 320, y: 300))
brush.line(to: NSPoint(x: 280, y: 360))
brush.line(to: NSPoint(x: 340, y: 400))
brush.line(to: NSPoint(x: 380, y: 350))
brush.line(to: NSPoint(x: 420, y: 410))
accentColor.setStroke()
brush.stroke()

// Sparkle trail (particles being swept)
for (x, y, r, a) in [(520, 580, 22, 0.9), (560, 520, 16, 0.7), (500, 460, 18, 0.8), (540, 410, 12, 0.6), (460, 340, 14, 0.5)] as [(CGFloat, CGFloat, CGFloat, CGFloat)] {
    let sparkle = NSBezierPath(ovalIn: NSRect(x: x-r/2, y: y-r/2, width: r, height: r))
    NSColor.white.withAlphaComponent(a).setFill()
    sparkle.fill()
}

// Code brackets on left
let bracketLeft = "&lt;/&gt;"
let attrs: [NSAttributedString.Key: Any] = [
    .font: NSFont.monospacedSystemFont(ofSize: 140, weight: .bold),
    .foregroundColor: NSColor.white.withAlphaComponent(0.15)
]
(bracketLeft as NSString).draw(at: NSPoint(x: 160, y: 420), withAttributes: attrs)

image.unlockFocus()

// Save base PNG
if let tiff = image.tiffRepresentation,
   let bitmap = NSBitmapImageRep(data: tiff),
   let png = bitmap.representation(using: .png, properties: [:]) {
    try! png.write(to: URL(fileURLWithPath: "Featherweight.iconset/icon_512x512@2x.png"))
    print("Base icon generated")
}
SWIFT

# Generate all required sizes from 1024px base
sips -z 16 16   "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_16x16.png" 2>/dev/null
sips -z 32 32   "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_16x16@2x.png" 2>/dev/null
sips -z 32 32   "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_32x32.png" 2>/dev/null
sips -z 64 64   "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_32x32@2x.png" 2>/dev/null
sips -z 128 128 "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_128x128.png" 2>/dev/null
sips -z 256 256 "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_128x128@2x.png" 2>/dev/null
sips -z 256 256 "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_256x256.png" 2>/dev/null
sips -z 512 512 "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_256x256@2x.png" 2>/dev/null
sips -z 512 512 "Featherweight.iconset/icon_512x512@2x.png" --out "Featherweight.iconset/icon_512x512.png" 2>/dev/null

# Package as .icns
iconutil -c icns "Featherweight.iconset" -o AppIcon.icns 2>/dev/null
rm -rf "Featherweight.iconset"
echo "AppIcon.icns generated"
ls -lh AppIcon.icns
