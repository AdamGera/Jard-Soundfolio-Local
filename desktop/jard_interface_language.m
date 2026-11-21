#import "jard_interface_language.h"

static NSString *const choiceKey = @"JardInterfaceLanguage";

static NSDictionary *catalog(void) {
    static NSDictionary *value;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSURL *file = [NSBundle.mainBundle URLForResource:@"interface-languages" withExtension:@"json"];
        NSData *data = file ? [NSData dataWithContentsOfURL:file] : nil;
        id decoded = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        value = [decoded isKindOfClass:NSDictionary.class] ? decoded : @{@"choices": @{@"en": @"English"}, @"messages": @{}};
    });
    return value;
}

NSString *JardResolveInterfaceLanguage(NSString *tag, NSDictionary *choices) {
    NSString *locale = [[tag.lowercaseString componentsSeparatedByCharactersInSet:
        [NSCharacterSet characterSetWithCharactersInString:@".@"]] firstObject];
    NSArray<NSString *> *parts = [[locale stringByReplacingOccurrencesOfString:@"_" withString:@"-"] componentsSeparatedByString:@"-"];
    NSString *language = parts.firstObject ?: @"en";
    if ([language isEqual:@"zh"]) {
        BOOL explicitSimplified = [parts containsObject:@"hans"];
        BOOL traditional = [parts containsObject:@"hant"] || [parts containsObject:@"tw"] ||
            [parts containsObject:@"hk"] || [parts containsObject:@"mo"];
        if (!explicitSimplified && traditional && choices[@"zh-Hant"]) return @"zh-Hant";
    }
    return choices[language] ? language : @"en";
}

static NSString *selectedLanguage(NSDictionary *choices) {
    id stored = [NSUserDefaults.standardUserDefaults objectForKey:choiceKey];
    return [stored isKindOfClass:NSString.class] && choices[stored] ? stored : @"system";
}

static NSString *resolvedLanguage(NSString *selected, NSDictionary *choices) {
    return [selected isEqual:@"system"] ? JardResolveInterfaceLanguage(NSLocale.preferredLanguages.firstObject, choices) : selected;
}

NSDictionary *JardInterfaceLanguageStatus(void) {
    NSDictionary *data = catalog();
    NSDictionary *choices = data[@"choices"];
    NSString *selected = selectedLanguage(choices), *resolved = resolvedLanguage(selected, choices);
    return @{@"selected": selected, @"resolved": resolved, @"choices": choices, @"messages": data[@"messages"][resolved] ?: @{}};
}

BOOL JardSetInterfaceLanguage(id choice) {
    if (![choice isKindOfClass:NSString.class]) return NO;
    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    if ([choice isEqual:@"system"]) [defaults removeObjectForKey:choiceKey];
    else if (catalog()[@"choices"][choice]) [defaults setObject:choice forKey:choiceKey];
    else return NO;
    return YES;
}

NSString *JardLocalized(NSString *source) {
    if (!source.length) return source;
    NSDictionary *data = catalog();
    NSDictionary *choices = data[@"choices"];
    return data[@"messages"][resolvedLanguage(selectedLanguage(choices), choices)][source] ?: source;
}
