/**
 * Human-facing quantity formatting, kept pure for tests.
 *
 * Values are stored in canonical units (meters, seconds, hertz…); showing
 * "705000 meter" to a person who wrote "705 km" would be technically true
 * and practically rude. Each kind picks a display scale by magnitude.
 */

type Scale = [number, string]

const SCALES: Record<string, Scale[]> = {
  length: [
    [1e12, 'Tm'],
    [1e3, 'km'],
    [1, 'm'],
    [1e-2, 'cm'],
    [1e-3, 'mm'],
    [1e-6, 'µm'],
    [1e-9, 'nm'],
  ],
  time: [
    [3.15576e7, 'yr'],
    [86400, 'd'],
    [3600, 'h'],
    [60, 'min'],
    [1, 's'],
    [1e-3, 'ms'],
    [1e-6, 'µs'],
  ],
  frequency: [
    [1e9, 'GHz'],
    [1e6, 'MHz'],
    [1e3, 'kHz'],
    [1, 'Hz'],
  ],
  power: [
    [1e6, 'MW'],
    [1e3, 'kW'],
    [1, 'W'],
    [1e-3, 'mW'],
  ],
  mass: [
    [1e3, 't'],
    [1, 'kg'],
    [1e-3, 'g'],
    [1e-6, 'mg'],
  ],
  pressure: [
    [1e6, 'MPa'],
    [1e3, 'kPa'],
    [1, 'Pa'],
  ],
  voltage: [
    [1e3, 'kV'],
    [1, 'V'],
    [1e-3, 'mV'],
  ],
  volume: [
    [1, 'L'],
    [1e-3, 'mL'],
    [1e-6, 'µL'],
    [1e-9, 'nL'],
  ],
  molarity: [
    [1, 'M'],
    [1e-3, 'mM'],
    [1e-6, 'µM'],
    [1e-9, 'nM'],
  ],
  mass_concentration: [
    [1, 'g/L'],
    [1e-3, 'mg/L'],
    [1e-6, 'µg/L'],
  ],
}

export function formatQuantity(
  valueSi: number | null,
  unitSi: string | null,
  kind: string,
): string {
  if (valueSi === null) return '?'
  if (kind === 'fraction') return `${round(valueSi * 100)} %`
  if (kind === 'level') return `${round(valueSi)} dB`
  if (kind === 'temperature') return `${round(valueSi)} K`
  if (kind === 'velocity') return `${round(valueSi)} m/s`

  const scales = SCALES[kind]
  if (!scales) {
    const magnitude = Math.abs(valueSi)
    if (magnitude !== 0 && (magnitude >= 1e4 || magnitude < 1e-2)) {
      return `${valueSi.toExponential(2)} ${unitSi ?? ''}`.trim()
    }
    return `${round(valueSi)} ${unitSi ?? ''}`.trim()
  }
  const magnitude = Math.abs(valueSi)
  const scale =
    scales.find(([factor]) => magnitude >= factor) ?? scales[scales.length - 1]!
  return `${round(valueSi / scale[0])} ${scale[1]}`
}

function round(value: number): string {
  const rounded = Math.abs(value) >= 100 ? value.toFixed(0) : value.toPrecision(3)
  return String(Number(rounded))
}
