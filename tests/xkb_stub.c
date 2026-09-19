/* Minimal stand-in for libxkbcommon, built by the test suite.
 *
 * The real library is not installed everywhere the tests run, but the ctypes
 * binding in ``ubuntu_clipboard.keymap`` is the one thing that cannot be
 * exercised with a Python double: a wrong struct layout or argument type would
 * only show up as garbage or a crash. This double speaks the same ABI, answers
 * for three layouts (us, ir, ru) and is deliberately small.
 */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef uint32_t xkb_keysym_t;

struct xkb_context {
    int unused;
};

struct xkb_keymap {
    char layout[32];
    char variant[32];
};

struct xkb_state {
    struct xkb_keymap *keymap;
};

struct xkb_rule_names {
    const char *rules;
    const char *model;
    const char *layout;
    const char *variant;
    const char *options;
};

/* Counters the tests read back with ctypes.in_dll(). */
int stub_contexts = 0;
int stub_keymaps = 0;
int stub_states = 0;
int stub_unrefs = 0;
char stub_last_variant[32] = {0};

#define STUB_MAX 16

struct xkb_context *xkb_context_new(int flags) {
    static struct xkb_context context;
    (void)flags;
    stub_contexts++;
    return &context;
}

void xkb_context_unref(struct xkb_context *context) {
    (void)context;
    stub_unrefs++;
}

struct xkb_keymap *xkb_keymap_new_from_names(struct xkb_context *context,
                                             const struct xkb_rule_names *names,
                                             int flags) {
    static struct xkb_keymap keymaps[STUB_MAX];
    struct xkb_keymap *keymap;
    (void)context;
    (void)flags;
    if (!names || !names->layout) {
        return 0;
    }
    keymap = &keymaps[stub_keymaps % STUB_MAX];
    stub_keymaps++;
    memset(keymap, 0, sizeof(*keymap));
    strncpy(keymap->layout, names->layout, sizeof(keymap->layout) - 1);
    strncpy(keymap->variant, names->variant ? names->variant : "", sizeof(keymap->variant) - 1);
    strncpy(stub_last_variant, keymap->variant, sizeof(stub_last_variant) - 1);
    return keymap;
}

void xkb_keymap_unref(struct xkb_keymap *keymap) {
    (void)keymap;
    stub_unrefs++;
}

struct xkb_state *xkb_state_new(struct xkb_keymap *keymap) {
    static struct xkb_state states[STUB_MAX];
    struct xkb_state *state;
    if (!keymap) {
        return 0;
    }
    state = &states[stub_states % STUB_MAX];
    stub_states++;
    state->keymap = keymap;
    return state;
}

void xkb_state_unref(struct xkb_state *state) {
    (void)state;
    stub_unrefs++;
}

xkb_keysym_t xkb_state_key_get_one_sym(struct xkb_state *state, uint32_t keycode) {
    const char *layout;
    if (!state || !state->keymap) {
        return 0;
    }
    if (keycode != 55) { /* only the V key is modelled */
        return 0;
    }
    layout = state->keymap->layout;
    if (strcmp(layout, "ir") == 0) {
        return 0x05D1; /* Arabic_ra, the legacy keysym */
    }
    if (strcmp(layout, "ru") == 0) {
        return 0x0100044C; /* Cyrillic_em as a Unicode keysym */
    }
    if (strcmp(layout, "us") == 0) {
        return 0x0076; /* v */
    }
    return 0;
}

int xkb_keysym_get_name(xkb_keysym_t keysym, char *buffer, size_t size) {
    const char *name = "";
    size_t length;
    if (keysym == 0x0076) {
        name = "v";
    } else if (keysym == 0x05D1) {
        name = "Arabic_ra";
    } else if (keysym == 0x0100044C) {
        name = "U044C";
    }
    length = strlen(name) + 1;
    if (size < length) {
        return 0;
    }
    memcpy(buffer, name, length);
    return (int)length;
}

int xkb_keysym_to_utf8(xkb_keysym_t keysym, char *buffer, size_t size) {
    const char *text = "";
    size_t length;
    if (keysym == 0x05D1) {
        text = "\xd8\xb1"; /* ر */
    } else if (keysym == 0x0100044C) {
        text = "\xd0\xbc"; /* м */
    }
    length = strlen(text) + 1;
    if (size < length) {
        return 0;
    }
    memcpy(buffer, text, length);
    return (int)length;
}
