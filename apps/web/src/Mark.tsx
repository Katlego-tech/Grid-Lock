/** The GridLock mark, from the responder-queue visual reference (docs/design/assets). */
export function Mark() {
  return (
    <svg className="mark-icon" viewBox="0 0 22 24" aria-hidden="true" focusable="false">
      <path
        d="M11 1.5 20.5 7v10L11 22.5 1.5 17V7z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
      />
      <path d="M11 8.2 15.2 10.6v4.8L11 17.8l-4.2-2.4v-4.8z" fill="currentColor" />
    </svg>
  );
}
