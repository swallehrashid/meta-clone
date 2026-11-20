// Order sounds and vibration (Settings -> Order sounds / Vibration). Generated, so nothing has to be downloaded.
let ctx: AudioContext | null = null

function tone(freqs: number[], dur = 0.09): void {
  try {
    ctx = ctx ?? new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)()
    let t = ctx.currentTime
    for (const f of freqs) {
      const o = ctx.createOscillator(), g = ctx.createGain()
      o.type = 'sine'; o.frequency.value = f
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.18, t + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t + dur)
      o.connect(g).connect(ctx.destination); o.start(t); o.stop(t + dur + 0.02)
      t += dur
    }
  } catch { /* no audio */ }
}

export const RINGTONES: Record<string, number[]> = {
  'Default (notification_004)': [880, 1175],
  'Chime': [659, 784, 988],
  'Ping': [1568],
  'Soft': [523, 659],
  'Silent': [],
}

export const sounds = {
  ok: () => tone([988, 1319]),          // order done
  error: () => tone([330, 247], 0.14),  // order refused
  ring: (name: string) => tone(RINGTONES[name] ?? RINGTONES['Default (notification_004)']),
}

export function vibrate(mode: string, ms = 30): void {
  if (mode === 'never') return
  try { navigator.vibrate?.(ms) } catch { /* unsupported */ }
}
