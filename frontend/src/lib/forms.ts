/**
 * Form validation in the app's own words. Forms set `noValidate`, so the browser's bubble
 * ("Please fill out this field") never shows; a form checks its fields on submit, shows each
 * message under its field in the Field's warning style, and moves focus to the first one.
 */
import { useEffect } from 'react'

export const REQUIRED = 'Required'
export const TWO_LETTER_STATE = 'Two-letter state code'
export const TEN_DIGITS = 'Ten digits'

export type FormErrors = Record<string, string>

/** `REQUIRED` for every field whose trimmed value is empty. */
export function missing<K extends string>(fields: Record<K, string>): Partial<Record<K, string>> {
  const errors: Partial<Record<K, string>> = {}
  for (const key of Object.keys(fields) as K[]) {
    if (fields[key].trim() === '') errors[key] = REQUIRED
  }
  return errors
}

/** A state field's message: blank is fine, anything but two letters is not. */
export function stateError(value: string): string | undefined {
  return value === '' || /^[A-Z]{2}$/.test(value) ? undefined : TWO_LETTER_STATE
}

/** An NPI field's message: blank is fine, anything but ten digits is not. */
export function npiError(value: string): string | undefined {
  const typed = value.trim()
  return typed === '' || /^\d{10}$/.test(typed) ? undefined : TEN_DIGITS
}

/** `errors` without `key`; the same object when the key is absent, so state does not churn. */
export function without(errors: FormErrors, key: string): FormErrors {
  if (!(key in errors)) return errors
  const { [key]: _dropped, ...rest } = errors
  return rest
}

/** Whenever `errors` gains entries, focus the form's first invalid input. */
export function useFocusInvalid(formId: string, errors: FormErrors): void {
  // A submit sets a new object each time, so the same mistakes twice focus the field again.
  useEffect(() => {
    if (Object.keys(errors).length === 0) return
    const form = document.getElementById(formId)
    form?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
  }, [formId, errors])
}
