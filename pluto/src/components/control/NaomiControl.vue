<script setup lang="ts">
// Target-owned panel for the NAOMI: the cabinet events a player's pad cannot send.
//
// Coin, Test and Service are NOT panel buttons. A coin goes into a slot, and test and
// service sit inside the coin door, so they belong to the cabinet rather than to whatever
// source happens to be driving the game. That is why they live here and not in a source
// mapping -- they stay available whichever source is selected.
//
// Each is a MOMENTARY switch on real hardware (the filter board exposes test and service
// as PSW1 and PSW2), so we send the existing drive protocol's hold down then up rather
// than inventing an event verb: a brief closure is exactly what the hardware does, and
// OpenJVS counts a coin on the press edge.
import { onMounted, ref } from 'vue'
import { driveBase } from '../../lib/drive'
import { API_BASE } from '../../composables/useNodes'
import UiButton from '../ui/UiButton.vue'
import UiSelect from '../ui/UiSelect.vue'
import UiSubTabs from '../ui/UiSubTabs.vue'

const tab = ref<'events' | 'controls'>('events')

// OpenJVS's game maps, read off the node that runs it: one map at a time, the one picked in
// the select (the one OpenJVS runs, its DEFAULT_GAME, until you pick another). Read only:
// picking a game shows its map, it does not change what OpenJVS runs.
interface MapRow { input: string; controller: string; output: string; player: string; file: string }
const jvsGames = ref<string[]>([])
const jvsCurrent = ref('')
const jvsGame = ref('')
const jvsRows = ref<MapRow[]>([])
const jvsError = ref('')
const jvsLoading = ref(false)
async function loadMap(game = jvsGame.value) {
  jvsLoading.value = true
  jvsError.value = ''
  try {
    const r = await fetch(`${API_BASE}/openjvs/map` + (game ? `?game=${encodeURIComponent(game)}` : ''))
    const j = await r.json().catch(() => null)
    if (!r.ok || !j) throw new Error(j?.error || `openjvs map -> ${r.status}`)
    jvsGames.value = Array.isArray(j.games) ? j.games : []
    jvsCurrent.value = j.current || ''
    jvsGame.value = j.game || ''
    jvsRows.value = Array.isArray(j.rows) ? j.rows : []
  } catch (e) {
    jvsError.value = e instanceof Error ? e.message : 'could not read the OpenJVS map'
  } finally {
    jvsLoading.value = false
  }
}
onMounted(() => loadMap(''))

const props = defineProps<{ active: boolean; target: string; source: string; mapping: string }>()
const emit = defineEmits<{ 'drive-error': [msg: string] }>()

const busy = ref('')
const note = ref('')

// How long the switch stays closed. Long enough that a poll cannot miss it (the board
// reads the IO board every frame), short enough to read as a press rather than a hold.
const CLOSURE_MS = 90

async function send(btn: string, down: boolean) {
  const r = await fetch(`${driveBase()}/control/drive`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'hold', down, btn, target: props.target, source: props.source, mapping: props.mapping }),
  })
  const j = await r.json().catch(() => null)
  if (j && j.ok === false) throw new Error(j.error || `${btn.toLowerCase()} failed`)
}

async function pulse(btn: string, label: string) {
  if (busy.value) return
  busy.value = btn
  note.value = ''
  try {
    await send(btn, true)
    await new Promise(r => setTimeout(r, CLOSURE_MS))
    await send(btn, false)
    note.value = label + ' sent'
    emit('drive-error', '')
  } catch (e) {
    // Release anyway: a switch left closed because the second call never happened would
    // read as a jammed coin mech or a held test button.
    try { await send(btn, false) } catch { /* already failed; nothing to recover */ }
    const msg = e instanceof Error ? e.message : 'failed'
    note.value = msg
    emit('drive-error', msg)
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div class="nc">
    <!-- A quarter of the screen holds one view at a time: the cabinet events or the controls map. -->
    <UiSubTabs v-model="tab" class="nc__tabs" :tabs="[{ key: 'events', label: 'Events' }, { key: 'controls', label: 'Controls' }]" />
    <div class="nc__body">
    <template v-if="tab === 'events'">
    <!-- Service then Test, matching the filter board silkscreen, which reads PSW2 PSW1
         left to right (PSW1 = Test, PSW2 = Service). The coin has no place on that board
         at all -- it arrives over JVS -- so it leads, as the one a player would use. -->
    <div class="nc__row">
      <!-- The coin is the slot, not a labelled button: the glyph IS the affordance.
           Title and aria-label carry the words for a screen reader and on hover. -->
      <UiButton variant="primary" class="nc__coin" title="Insert Coin" aria-label="Insert Coin"
        :disabled="!active || busy === 'COIN'" @click="pulse('COIN', 'Coin')">🪙</UiButton>
      <UiButton :disabled="!active || busy === 'SERVICE'" @click="pulse('SERVICE', 'Service')">Service</UiButton>
      <UiButton :disabled="!active || busy === 'TEST'" @click="pulse('TEST', 'Test')">Test</UiButton>
    </div>
    <p v-if="note" class="nc__note">{{ note }}</p>
    </template>

    <template v-else>
    <div class="nc__map-head">
      <p class="nc__lbl">OpenJVS map</p>
      <UiSelect v-if="jvsGames.length" class="nc__pick" :model-value="jvsGame" :disabled="jvsLoading"
        @update:model-value="loadMap">
        <option v-for="g in jvsGames" :key="g" :value="g">{{ g }}{{ g === jvsCurrent ? ' (running)' : '' }}</option>
      </UiSelect>
      <UiButton class="nc__reload" :disabled="jvsLoading" title="Read the map again" @click="loadMap()">Reload</UiButton>
    </div>
    <p v-if="jvsError" class="nc__note">{{ jvsError }}</p>
    <p v-else-if="jvsLoading && !jvsRows.length" class="nc__note">Reading the map…</p>
    <p v-else-if="!jvsGame" class="nc__note">OpenJVS names no DEFAULT_GAME: pick a game to see its map.</p>
    <table v-else class="nc__map">
      <tbody>
        <tr v-for="(r, i) in jvsRows" :key="i" :title="'from games/' + r.file">
          <td>{{ r.input }}</td>
          <td>{{ r.controller }}</td>
          <td class="nc__out">{{ r.output }}</td>
          <td>{{ r.player }}</td>
        </tr>
      </tbody>
    </table>
    </template>
    </div>
  </div>
</template>

<style scoped>
/* The quadrant clips (overflow: hidden): the tabs stay put and the body scrolls itself, since the map can be longer than the cell. */
.nc { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
/* A fixed 40px bar (tabs stretched to it, underline at the bottom) so the quadrant's fullscreen
   button can centre on it exactly: ControlLayout's .quad--tabbed puts the 28px button at top 6px. */
.nc__tabs { padding-left: 12px; padding-right: 12px; height: 40px; box-sizing: border-box; align-items: stretch; }
/* The tab's main area: the same white as the tab bar above it, so the two read as one surface. */
.nc__body { flex: 1; min-height: 0; overflow: auto; padding: 12px; background: var(--surface); }
.nc__lbl { margin: 0 0 8px; font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }
.nc__row { display: flex; flex-wrap: wrap; gap: 8px; }
.nc__coin { font-size: 17px; line-height: 1; padding-left: 14px; padding-right: 14px; }
.nc__note { margin: 10px 0 0; font-size: 12px; color: var(--text-muted); }
.nc__map-head { display: flex; align-items: center; gap: 8px; }
.nc__map-head .nc__lbl { margin: 0; white-space: nowrap; }
.nc__pick { flex: 1; min-width: 0; font-family: var(--font-mono); }
.nc__map { width: 100%; margin-top: 8px; border-collapse: collapse; font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
.nc__map td { padding: 3px 8px 3px 0; border-top: 1px solid var(--line); white-space: nowrap; }
.nc__out { color: var(--text); }
</style>
