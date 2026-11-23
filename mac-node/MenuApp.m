#import <Cocoa/Cocoa.h>
#import <unistd.h>

@interface SirusMenu : NSObject <NSApplicationDelegate>
@property(nonatomic,strong) NSStatusItem *statusItem;
@end

@implementation SirusMenu
- (NSString *)service { return [NSString stringWithFormat:@"gui/%u/com.sirussofts.mac-node",getuid()]; }
- (NSString *)plist { return [NSHomeDirectory() stringByAppendingPathComponent:@"Library/LaunchAgents/com.sirussofts.mac-node.plist"]; }
- (int)launchctl:(NSArray<NSString *> *)args {
    NSTask *task=[NSTask new]; task.launchPath=@"/bin/launchctl"; task.arguments=args;
    task.standardOutput=[NSFileHandle fileHandleWithNullDevice];
    task.standardError=[NSFileHandle fileHandleWithNullDevice];
    @try { [task launch]; [task waitUntilExit]; return task.terminationStatus; }
    @catch(NSException *ex) { return -1; }
}
- (void)alert:(NSString *)message { NSAlert *a=[NSAlert new]; a.messageText=@"SirusSofts Mac Node"; a.informativeText=message; [a runModal]; }
- (void)add:(NSString *)title action:(SEL)action menu:(NSMenu *)menu {
    NSMenuItem *item=[[NSMenuItem alloc] initWithTitle:title action:action keyEquivalent:@""];
    item.target=self; [menu addItem:item];
}
- (void)applicationDidFinishLaunching:(NSNotification *)n {
    self.statusItem=[[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.title=@"Sirus";
    NSMenu *m=[NSMenu new];
    [self add:@"Service status" action:@selector(status:) menu:m];
    [m addItem:[NSMenuItem separatorItem]];
    [self add:@"Start worker" action:@selector(start:) menu:m];
    [self add:@"Stop worker" action:@selector(stop:) menu:m];
    [self add:@"Mac dashboard" action:@selector(dashboard:) menu:m];
    [self add:@"Open configuration" action:@selector(config:) menu:m];
    [m addItem:[NSMenuItem separatorItem]];
    [self add:@"Quit menu (worker continues)" action:@selector(quit:) menu:m];
    self.statusItem.menu=m;
}
- (void)status:(id)sender {
    [self alert:[self launchctl:@[@"print",[self service]]]==0 ? @"launchd worker loaded; check /mac for heartbeat." : @"Worker not loaded."];
}
- (void)start:(id)sender {
    if(![[NSFileManager defaultManager] fileExistsAtPath:[self plist]]) { [self alert:@"Install worker first."]; return; }
    NSString *domain=[NSString stringWithFormat:@"gui/%u",getuid()];
    if([self launchctl:@[@"bootstrap",domain,[self plist]]]!=0 && [self launchctl:@[@"print",[self service]]]!=0) {
        [self alert:@"Unable to bootstrap launchd service."]; return;
    }
    if([self launchctl:@[@"kickstart",@"-k",[self service]]]!=0) [self alert:@"Unable to start worker; inspect stderr.log."];
}
- (void)stop:(id)sender {
    if([self launchctl:@[@"bootout",[self service]]]!=0) [self alert:@"Worker already stopped or stop failed."];
}
- (void)dashboard:(id)sender { [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"https://sirussofts.com/mac"]]; }
- (void)config:(id)sender {
    NSString *p=[NSHomeDirectory() stringByAppendingPathComponent:@".config/sirus-mac-node/config.json"];
    [[NSWorkspace sharedWorkspace] openFile:p];
}
- (void)quit:(id)sender { [NSApp terminate:nil]; }
@end

int main(int argc,const char *argv[]) {
    @autoreleasepool {
        NSApplication *app=[NSApplication sharedApplication];
        SirusMenu *delegate=[SirusMenu new];
        app.delegate=delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
