/**
 * What is behind the columns: it shows in the gaps, under the dock and through
 * the glass chat. A deep blue-black instead of flat black, two large soft glows
 * in the mode's colours (`--backdrop-a` / `--backdrop-b` in index.css), and a
 * faint grid, as on Lumen's stage.
 *
 * Still, on purpose. It used to drift, and the header, the dock and the chat
 * are glass (`backdrop-filter`): anything moving behind a backdrop blur makes
 * the browser blur it again every frame. The whole workspace fell to ~5 fps
 * under software rendering while a blank page and the sign-in page ran at 60.
 * The glows are radial gradients, soft by themselves — no blur filter either.
 */
export function AppBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      <div className="absolute inset-0 bg-[linear-gradient(160deg,#070c16_0%,#05070c_55%,#080611_100%)]" />
      <div
        className="absolute -top-[35%] -left-[20%] size-[80vmax] rounded-full opacity-[0.22]"
        style={{ background: 'radial-gradient(circle, var(--backdrop-a) 0%, transparent 60%)' }}
      />
      <div
        className="absolute -right-[25%] -bottom-[40%] size-[85vmax] rounded-full opacity-[0.2]"
        style={{ background: 'radial-gradient(circle, var(--backdrop-b) 0%, transparent 60%)' }}
      />
      <div
        className="absolute inset-0 opacity-[0.035]"
        style={{
          backgroundImage:
            'linear-gradient(#cbd5e1 1px, transparent 1px), linear-gradient(90deg, #cbd5e1 1px, transparent 1px)',
          backgroundSize: '56px 56px',
          maskImage: 'radial-gradient(ellipse at 50% 40%, black 30%, transparent 80%)',
        }}
      />
    </div>
  )
}
