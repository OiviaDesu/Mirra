#import <Foundation/Foundation.h>
#import <objc/runtime.h>

// This is the accepted behavior formerly selected by experiment 11.
#define MIRRA_MEDIA_PROFILE "awdl-single-tile"

typedef NS_ENUM(NSUInteger, MirroringMediaMethod) {
    MirroringMediaSendRequest,
    MirroringMediaTiles,
    MirroringMediaTrafficFlags,
    MirroringMediaStreamFlags,
    MirroringMediaMethodCount
};

NSDictionary *MirroringMediaCompatibilityReport(void);
NSDictionary *MirroringHostCompatibilityReport(void);
BOOL MirroringMediaHostSupported(NSString *architecture, NSString *version);
Method MirroringMediaRequiredMethod(MirroringMediaMethod entry);
BOOL MirroringInstallMediaProfile(void);
