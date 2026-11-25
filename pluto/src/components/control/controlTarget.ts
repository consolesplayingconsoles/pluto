// The selected Control target's id ('naomi', 'kindle', a pico alias...), provided by
// ControlTab and injected by ControlLayout. It travels this way, not as a prop, so the
// target-owned panels (SW) never depend on a source component passing it along: every
// source gets them, and a new source cannot forget them.
import type { ComputedRef, InjectionKey } from 'vue'

export const CONTROL_TARGET_ID: InjectionKey<ComputedRef<string>> = Symbol('controlTargetId')
