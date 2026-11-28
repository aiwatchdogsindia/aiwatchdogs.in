export function canSpeak(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined';
}

export function findVoice(bcp47: string): SpeechSynthesisVoice | null {
  if (!canSpeak()) return null;
  const voices = window.speechSynthesis.getVoices();
  const want = bcp47.toLowerCase();
  const base = want.split('-')[0];
  return (
    voices.find((v) => v.lang.toLowerCase() === want) ||
    voices.find((v) => v.lang.toLowerCase().replace('_', '-').startsWith(`${base}-`)) ||
    voices.find((v) => v.lang.toLowerCase() === base) ||
    null
  );
}

function chunks(text: string, size = 220): string[] {
  const parts = text.split(/(?<=[.!?।۔。？！])\s+/);
  const out: string[] = [];
  let cur = '';
  for (const p of parts) {
    if ((cur + ' ' + p).length > size && cur) {
      out.push(cur);
      cur = p;
    } else {
      cur = cur ? `${cur} ${p}` : p;
    }
  }
  if (cur) out.push(cur);
  return out;
}

export function speak(text: string, bcp47: string, onEnd?: () => void): boolean {
  if (!canSpeak()) return false;
  const synth = window.speechSynthesis;
  synth.cancel();
  const voice = findVoice(bcp47);
  const pieces = chunks(text);
  pieces.forEach((piece, i) => {
    const u = new SpeechSynthesisUtterance(piece);
    u.lang = voice?.lang || bcp47;
    if (voice) u.voice = voice;
    u.rate = 0.95;
    if (i === pieces.length - 1 && onEnd) {
      u.onend = onEnd;
      u.onerror = onEnd;
    }
    synth.speak(u);
  });
  return Boolean(voice);
}

export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel();
}
