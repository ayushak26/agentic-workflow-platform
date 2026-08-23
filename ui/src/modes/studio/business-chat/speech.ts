export type SpeechRecognitionResult = { transcript: string; final: boolean };

type RecognitionAlternative = { transcript: string };
type RecognitionResultLike = { isFinal: boolean; 0: RecognitionAlternative };
type RecognitionEventLike = { resultIndex: number; results: ArrayLike<RecognitionResultLike> };

export interface BrowserSpeechRecognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: RecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type RecognitionConstructor = new () => BrowserSpeechRecognition;

export function recognitionConstructor(scope: Window = window): RecognitionConstructor | null {
  const candidate = scope as Window & {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };
  return candidate.SpeechRecognition ?? candidate.webkitSpeechRecognition ?? null;
}

export function speechRecognitionSupported(scope: Window = window): boolean {
  return recognitionConstructor(scope) !== null;
}

export function speechSynthesisSupported(scope: Window = window): boolean {
  return 'speechSynthesis' in scope && 'SpeechSynthesisUtterance' in scope;
}

export function collectRecognitionText(event: RecognitionEventLike): SpeechRecognitionResult {
  let transcript = '';
  let final = true;
  for (let index = event.resultIndex; index < event.results.length; index += 1) {
    const result = event.results[index];
    transcript += result?.[0]?.transcript ?? '';
    final = final && Boolean(result?.isFinal);
  }
  return { transcript: transcript.trim(), final };
}

export function speakText(text: string, scope: Window = window): boolean {
  if (!text.trim() || !speechSynthesisSupported(scope)) return false;
  scope.speechSynthesis.cancel();
  const Utterance = (scope as Window & {
    SpeechSynthesisUtterance: typeof SpeechSynthesisUtterance;
  }).SpeechSynthesisUtterance;
  const utterance = new Utterance(text);
  utterance.rate = 1;
  utterance.pitch = 1;
  scope.speechSynthesis.speak(utterance);
  return true;
}

export function stopSpeaking(scope: Window = window): void {
  if ('speechSynthesis' in scope) scope.speechSynthesis.cancel();
}