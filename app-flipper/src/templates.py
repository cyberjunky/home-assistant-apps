"""What a new project starts with."""

TEMPLATES = {
    "screen": "C app: draws on the screen and reads the buttons",
    "minimal": "C app: the bare minimum (ufbt's own template)",
    "js": "JavaScript: a script the Flipper runs without a build",
}

_MANIFEST = """App(
    appid="{appid}",  # Must be unique
    name="{title}",  # Displayed in menus
    apptype=FlipperAppType.EXTERNAL,
    entry_point="{appid}_app",
    stack_size=2 * 1024,
    fap_category="Tools",  # The folder under Apps on the Flipper
    # fap_version="0.1",
    # fap_icon="{appid}.png",  # 10x10 1-bit PNG
    # fap_description="A simple app",
    # fap_author="J. Doe",
)
"""

_SCREEN_C = """#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>

// What the app remembers between a button press and the next redraw
typedef struct {{
    FuriMutex* mutex;
    uint32_t count;
}} AppState;

// Called by the system whenever the screen must be redrawn. The screen is 128 x 64 pixels.
static void draw_callback(Canvas* canvas, void* context) {{
    AppState* state = context;
    furi_mutex_acquire(state->mutex, FuriWaitForever);
    uint32_t count = state->count;
    furi_mutex_release(state->mutex);

    char line[32];
    snprintf(line, sizeof(line), "Pressed: %lu", (unsigned long)count);

    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 4, 12, "{title}");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 4, 30, line);
    canvas_draw_str(canvas, 4, 60, "OK: count   Back: exit");
}}

// Called on every button event; hands it to the main loop below
static void input_callback(InputEvent* event, void* context) {{
    FuriMessageQueue* queue = context;
    furi_message_queue_put(queue, event, FuriWaitForever);
}}

int32_t {appid}_app(void* p) {{
    UNUSED(p);
    AppState state = {{.mutex = furi_mutex_alloc(FuriMutexTypeNormal), .count = 0}};
    FuriMessageQueue* queue = furi_message_queue_alloc(8, sizeof(InputEvent));

    ViewPort* view_port = view_port_alloc();
    view_port_draw_callback_set(view_port, draw_callback, &state);
    view_port_input_callback_set(view_port, input_callback, queue);

    Gui* gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(gui, view_port, GuiLayerFullscreen);

    FURI_LOG_I("{appid}", "started");

    InputEvent event;
    bool running = true;
    while(running) {{
        if(furi_message_queue_get(queue, &event, 100) != FuriStatusOk) continue;
        if(event.type != InputTypeShort) continue;
        if(event.key == InputKeyBack) {{
            running = false;
        }} else if(event.key == InputKeyOk) {{
            furi_mutex_acquire(state.mutex, FuriWaitForever);
            state.count++;
            furi_mutex_release(state.mutex);
            view_port_update(view_port);
        }}
    }}

    view_port_enabled_set(view_port, false);
    gui_remove_view_port(gui, view_port);
    furi_record_close(RECORD_GUI);
    view_port_free(view_port);
    furi_message_queue_free(queue);
    furi_mutex_free(state.mutex);
    return 0;
}}
"""

_MINIMAL_C = """#include <furi.h>

int32_t {appid}_app(void* p) {{
    UNUSED(p);
    FURI_LOG_I("TEST", "Hello world");
    FURI_LOG_I("TEST", "I'm {appid}!");
    return 0;
}}
"""

_JS = """// {title}: runs on the Flipper as it is, no build needed.
// What print() writes shows on the Flipper's screen. Hold Back on the Flipper to stop a script.
let notify = require("notification");

print("Hello from Home Assistant");
for (let i = 3; i > 0; i--) {{
    print("Blink", i);
    notify.blink("blue", "short");
    delay(700);
}}
notify.success();
print("Done");
delay(1500);
"""


def render(template: str, appid: str, title: str) -> dict[str, str] | None:
    """The files of a new project: path to text."""
    title = title.replace('"', "'").replace("\\", "")[:40]
    values = {"appid": appid, "title": title}
    if template == "screen":
        return {"application.fam": _MANIFEST.format(**values), f"{appid}.c": _SCREEN_C.format(**values)}
    if template == "minimal":
        return {"application.fam": _MANIFEST.format(**values), f"{appid}.c": _MINIMAL_C.format(**values)}
    if template == "js":
        return {f"{appid}.js": _JS.format(**values)}
    return None
