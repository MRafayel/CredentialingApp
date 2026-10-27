/**
 * The NPI check digit: Luhn over the prefix 80840 and the first nine digits.
 * The UI only warns with it; the backend decides whether an NPI is accepted.
 */

export type NpiState = 'empty' | 'incomplete' | 'mismatch' | 'valid'

/** Whether a ten-digit NPI's last digit matches its check digit. */
export function hasValidCheckDigit(npi: string): boolean {
  if (!/^\d{10}$/.test(npi)) return false
  let total = 0
  const digits = `80840${npi.slice(0, 9)}`
  for (let index = 0; index < digits.length; index += 1) {
    let digit = Number(digits[digits.length - 1 - index])
    if (index % 2 === 0) {
      digit *= 2
      if (digit > 9) digit -= 9
    }
    total += digit
  }
  return (10 - (total % 10)) % 10 === Number(npi[9])
}

/** What to tell someone typing an NPI. */
export function npiState(value: string): NpiState {
  if (value === '') return 'empty'
  if (!/^\d{10}$/.test(value)) return 'incomplete'
  return hasValidCheckDigit(value) ? 'valid' : 'mismatch'
}
