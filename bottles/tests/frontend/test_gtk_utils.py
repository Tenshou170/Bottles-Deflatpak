# ruff: noqa: E402

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk, Pango

from bottles.frontend.utils.gtk import GtkUtils


def _drain_main_loop(timeout_ms=2000):
    """Run the GLib main loop until no idle sources remain, bounded."""
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + timeout_ms * 1000
    while GLib.get_monotonic_time() < deadline:
        if not context.pending():
            return True
        context.iteration(False)
    return False


def test_populate_in_chunks_builds_every_item():
    built = []
    items = list(range(25))

    GtkUtils.populate_in_chunks(items, built.append, chunk_size=8)
    # First batch is synchronous.
    assert built == list(range(8))

    assert _drain_main_loop()
    assert built == items


def test_populate_in_chunks_handles_small_list_synchronously():
    built = []

    GtkUtils.populate_in_chunks(["a", "b", "c"], built.append, chunk_size=8)

    # A list smaller than one chunk is fully built before returning.
    assert built == ["a", "b", "c"]


def test_populate_in_chunks_empty_list_is_noop():
    built = []

    GtkUtils.populate_in_chunks([], built.append)

    assert built == []


def test_populate_in_chunks_stops_when_cancelled():
    built = []
    items = list(range(40))
    cancel = {"flag": False}

    def build(item):
        built.append(item)
        # Simulate the list being rebuilt partway through the fill.
        if item == 9:
            cancel["flag"] = True

    GtkUtils.populate_in_chunks(
        items, build, chunk_size=8, cancelled=lambda: cancel["flag"]
    )
    assert _drain_main_loop()

    # Filling stops at the cancellation point; later items are never built.
    assert built == list(range(10))


def test_full_width_string_list_factory_uses_unellipsized_labels():
    factory = GtkUtils.create_full_width_string_list_factory()
    list_item = Gtk.ListItem()

    factory.emit("setup", list_item)

    label = list_item.get_child()
    assert isinstance(label, Gtk.Label)
    assert label.get_ellipsize() == Pango.EllipsizeMode.NONE
    assert label.get_wrap() is False
    assert label.get_xalign() == 0
