#import <Foundation/Foundation.h>
#import "meeting_presence.h"

@interface JardCalendarMeeting : NSObject
@property(nonatomic, copy) NSString *identifier, *title, *calendar, *provider;
@property(nonatomic, copy) NSDate *start, *end;
@property(nonatomic, copy) NSURL *link;
@property(nonatomic, copy) NSArray<NSString *> *attendees;
@end

JardCalendarMeeting *JardCalendarOccurrence(NSString *key, NSString *title, NSString *calendar,
    NSDate *start, NSDate *end, BOOL excluded, NSArray<NSString *> *attendees, NSArray<NSString *> *fields);
BOOL JardCalendarMatchesCall(JardCalendarMeeting *meeting, JardCall *call);

@interface JardCalendar : NSObject
- (instancetype)initWithChanged:(void (^)(void))changed;
@property(nonatomic, readonly) BOOL connected, reminders;
@property(nonatomic, readonly) NSInteger lead;
@property(nonatomic, readonly) NSArray<JardCalendarMeeting *> *meetings;
- (NSDictionary *)statusAt:(NSDate *)now;
- (void)refreshAt:(NSDate *)now;
- (void)connect:(void (^)(NSString *error))reply;
- (void)disconnect;
- (void)setReminders:(BOOL)enabled;
- (void)setLead:(NSInteger)minutes;
- (BOOL)setIncluded:(BOOL)included calendar:(NSString *)identifier;
- (void)shutdown;
@end
