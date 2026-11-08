// Test-only simulation of a changed framework method encoding. Loaded into
// the probe process only; never part of the native support package.
#include <objc/runtime.h>
#include <string.h>

static const char *mismatched_encoding(Method method) {
    if (method && !strcmp(sel_getName(method_getName(method)), "setTrafficFlags:")) return "v16@0:8";
    return method_getTypeEncoding(method);
}
__attribute__((used)) static const struct { const void *replacement; const void *original; }
interpose_encoding __attribute__((section("__DATA,__interpose"))) =
    {(const void *)mismatched_encoding, (const void *)method_getTypeEncoding};
