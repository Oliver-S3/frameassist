/* Pass an overlay-local point through OpenVR.
 *
 * GetTransformForOverlayCoordinates takes HmdVector2_t BY VALUE. ctypes on
 * aarch64 does not pass that struct the way the runtime reads it, so calling
 * it directly from Python silently yields a wrong matrix -- in practice the
 * same pose for every overlay and insensitive to the coordinates fed in.
 * This translation unit performs the call with a real by-value struct.
 *
 * Adapted from whisper-frame (MIT):
 *   https://github.com/groffta/whisper-frame/blob/main/overlay_point.c
 * Kept deliberately headerless so it builds with a bare `gcc -shared -fPIC`.
 */
#include <stdint.h>

typedef struct {
    float v[2];
} HmdVector2_t;

/* IVROverlay::GetTransformForOverlayCoordinates signature.
 * origin is an ETrackingUniverseOrigin (1 = Standing). */
typedef int (*GetCoordsFn)(uint64_t handle, int origin, HmdVector2_t coords,
                           float *mat);

/* Returns 0 on success. out receives a 3x4 (12 float) row-major matrix. */
int frameassist_overlay_point(void *fn, uint64_t handle, int origin,
                              float x, float y, float out[12]) {
    HmdVector2_t coords;
    coords.v[0] = x;
    coords.v[1] = y;
    return ((GetCoordsFn)fn)(handle, origin, coords, out);
}
