import AppKit

@main
struct IndicatorTests {
    @MainActor static func main() {
        let scope = DotScopeSimulation()
        scope.restart(reduceMotion: false)
        func render(_ stage: DotScopeStage, loudness: Float, visible: Bool = true, frames: Int = 60) {
            for _ in 0..<frames {
                scope.advance(dt: 1.0 / 60.0, stage: stage, visible: visible, loudness: loudness, reduceMotion: false)
            }
        }
        render(.recording, loudness: -70, frames: 2)
        let entrance = scope.pose
        render(.recording, loudness: -70)
        let quiet = scope.dots
        precondition(entrance.width < scope.pose.width && abs(scope.pose.width - 112) < 0.01)
        precondition(scope.columns == 43 && scope.rows == 9)
        render(.recording, loudness: -26)
        precondition(scope.dots != quiet && scope.pose.opacity > 0.99)
        render(.processing, loudness: -70)
        precondition(abs(scope.pose.width - 76) < 0.01 && scope.pose.cool > 0.99)
        render(.done, loudness: -70, visible: false)
        precondition(scope.settled)

        let meter = JardDictationMeter(frame: CGRect(x: 0, y: 0, width: 142, height: 50))
        meter.setStage(0, label: "Listening")
        for frame in 0..<60 { meter.advance(at: Double(frame) / 60.0, rms: 0.03) }
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 142, pixelsHigh: 50,
                                      bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                      colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        meter.draw(meter.bounds)
        NSGraphicsContext.restoreGraphicsState()
        precondition((bitmap.colorAt(x: 71, y: 25)?.alphaComponent ?? 0) > 0.8)
        precondition((bitmap.colorAt(x: 0, y: 0)?.alphaComponent ?? 1) < 0.1)
        precondition(meter.subviews.isEmpty && meter.accessibilityLabel() == "Listening")
        print("Dot Scope entrance, speech response, processing, dismissal, and text-free AppKit drawing passed.")
    }
}
