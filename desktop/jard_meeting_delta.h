#import <Foundation/Foundation.h>

NSArray<NSDictionary *> *JardMeetingTurns(id rows);
NSString *JardMeetingExcerptText(NSArray<NSDictionary *> *turns, NSDictionary *labels);

@interface JardMeetingDelta : NSObject
- (NSArray<NSDictionary *> *)unseenTurns:(NSArray<NSDictionary *> *)turns meeting:(NSString *)identifier;
- (void)deliveredTurns:(NSArray<NSDictionary *> *)turns meeting:(NSString *)identifier;
- (void)replaceMeeting:(NSString *)before with:(NSString *)after;
@end
