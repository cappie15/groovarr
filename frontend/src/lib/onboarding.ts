/**
 * First-run Setup Wizard dismissal — per-browser only, not a real Settings
 * field. The wizard's own state comes entirely from GET /api/settings (the
 * same source of truth the flat Settings page uses); this is *only* "has
 * this browser already been shown, or chosen to skip, the auto-popup" so
 * that dismissing it doesn't require inventing a backend field purely for
 * onboarding chrome. Visiting /setup directly (e.g. the "Run the Setup
 * Wizard" link on the Settings page) always works regardless of this flag —
 * it only gates the *automatic* redirect in App.tsx.
 */

const DISMISSED_KEY = 'groovarr:setupWizardDismissed';

export function isSetupWizardDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) === '1';
  } catch {
    // Private browsing / blocked storage: fail open on the safe side —
    // worst case the wizard offers itself again next load.
    return false;
  }
}

export function dismissSetupWizard(): void {
  try {
    window.localStorage.setItem(DISMISSED_KEY, '1');
  } catch {
    // Non-fatal — see isSetupWizardDismissed().
  }
}
