/** Closes the nearest ancestor <details> dropdown after a menu item inside it
 *  is clicked — used for the "⋯ more actions" pattern (a <details> styled as
 *  a popover), which otherwise stays open until the user clicks elsewhere. */
export function closeDetailsMenu(e: { currentTarget: EventTarget | null }) {
  (e.currentTarget as HTMLElement | null)?.closest("details")?.removeAttribute("open");
}
