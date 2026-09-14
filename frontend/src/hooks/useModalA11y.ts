import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Minimal keyboard-accessibility contract for the app's hand-rolled
 * `.modal-overlay`/`.modal` dialogs (Connect/Playlists/Settings all define
 * their own inline, rather than sharing one <Modal> component — this hook
 * is the piece they *can* share). Before this, opening one of these left
 * the page behind the overlay fully tabbable (a keyboard user could Tab
 * straight from a dialog button into invisible content behind it), moved
 * focus nowhere on open, and did not respond to Escape.
 *
 * Attach the returned ref to the dialog's `.modal` element (not the
 * `.modal-overlay` backdrop). While `active` is true this:
 *  - moves focus to the dialog's first focusable element on open (or the
 *    dialog container itself, via `tabIndex={-1}`, if it has none),
 *  - traps Tab/Shift+Tab so focus cycles within the dialog,
 *  - calls `onClose` on Escape,
 *  - restores focus to whatever element had it before the dialog opened,
 *    once the dialog closes or unmounts.
 */
export function useModalA11y<T extends HTMLElement>(active: boolean, onClose: () => void) {
  const containerRef = useRef<T>(null);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;

    function focusables(): HTMLElement[] {
      return Array.from(container!.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    }

    const initial = focusables()[0] ?? container;
    initial.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    // Capture phase: this dialog owns Escape/Tab while it's open, ahead of
    // any other page-level handler.
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      previouslyFocused?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return containerRef;
}
