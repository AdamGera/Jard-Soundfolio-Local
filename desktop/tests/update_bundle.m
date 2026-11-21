#import "../jard_updater.h"
#import <assert.h>

int main(int argc, const char **argv) {
    @autoreleasepool {
        assert(argc == 5);
        NSURL *installed = [NSURL fileURLWithPath:@(argv[1])];
        NSURL *candidate = [NSURL fileURLWithPath:@(argv[2])];
        NSURL *foreign = [NSURL fileURLWithPath:@(argv[3])];
        NSURL *tampered = [NSURL fileURLWithPath:@(argv[4])];
        NSString *reason = nil;
        assert(JardVerifyUpdateBundle(installed, candidate, @"1.2.0", &reason));
        assert(!JardVerifyUpdateBundle(installed, candidate, @"1.3.0", &reason));
        assert(!JardVerifyUpdateBundle(installed, foreign, @"1.2.0", &reason));
        assert(!JardVerifyUpdateBundle(installed, tampered, @"1.2.0", &reason));
        assert(!JardSwapUpdateBundles(installed, [tampered URLByAppendingPathComponent:@"absent"], &reason));
        assert(JardVerifyUpdateBundle(installed, candidate, @"1.2.0", &reason));
        assert(JardSwapUpdateBundles(installed, candidate, &reason));
        assert(JardVerifyUpdateBundle(candidate, installed, @"1.2.0", &reason));
        assert(JardVerifyUpdateBundle(installed, candidate, @"1.1.0", &reason));
        puts("Updater verifies identity and version, rejects tampering, and atomically exchanges bundles.");
    }
}
