import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Ionicons from '@expo/vector-icons/Ionicons';

import { api, type Place, type SearchResponse } from '../lib/api';
import { distance, placeIcon } from '../lib/format';
import { loadRecents } from '../lib/recents';
import { useTheme } from '../lib/theme';

/** Wait this long after the last keystroke before asking the server.
 *
 * NOT A PERFORMANCE TUNING. Nominatim's usage policy asks that an autocomplete send at
 * most one request per input rather than one per keystroke, and Photon asks for the same
 * restraint informally. 300 ms is below the pause between words for most typists, so the
 * search fires roughly once per thing typed -- which is what the policy is asking for.
 */
const DEBOUNCE_MS = 300;

/** Below this the server answers empty, so there is nothing to send. */
const MIN_QUERY = 2;

/** A key that is stable across re-renders and unique per row. The name is neither: OSM
 * has six 7-Elevens in the CBD and they are all called 7-Eleven. */
const keyOf = (p: Place) => `${p.lat.toFixed(6)},${p.lon.toFixed(6)},${p.name}`;

function Row({ place, onPress, color }: { place: Place; onPress: () => void; color: string }) {
  // ONLY `false` earns a badge. `open_now` is three-valued and null means OSM carries no
  // opening_hours for this place -- which is most of them, and every street. Rendering
  // null as Closed would mark half the CBD shut on data that does not exist.
  const closed = place.open_now === false;
  return (
    <Pressable
      onPress={onPress}
      className="flex-row items-center gap-3 border-b border-line px-4 py-3 dark:border-line-dark active:bg-paper-sunken dark:active:bg-night"
    >
      <Ionicons name={placeIcon(place.kind) as any} size={20} color={color} />
      <View className="flex-1">
        <View className="flex-row items-center gap-2">
          <Text
            className={`shrink text-base ${closed ? 'text-ink-soft' : 'text-ink dark:text-paper'}`}
            numberOfLines={1}
          >
            {place.name}
          </Text>
          {closed ? (
            <View className="rounded-full bg-paper-sunken px-2 py-0.5 dark:bg-night-raised">
              <Text className="text-[11px] font-medium text-ink-soft">Closed</Text>
            </View>
          ) : null}
        </View>
        {/* The hours themselves, not just the verdict: "Closed" alone invites "closed
            until when?", and the raw OSM string already answers it. */}
        {closed && place.opening_hours ? (
          <Text className="text-[13px] text-ink-soft" numberOfLines={1}>
            {place.opening_hours}
          </Text>
        ) : place.address ? (
          <Text className="text-[13px] text-ink-soft" numberOfLines={1}>
            {place.address}
          </Text>
        ) : null}
      </View>
      {/* Straight-line, so it is never longer than the walk -- and it is the only thing
          on the row that separates five Hungry Jack's from each other. Its own column
          rather than appended to the address, which is already the line that truncates. */}
      {typeof place.distance_m === 'number' ? (
        <Text className="text-[13px] text-ink-soft">{distance(place.distance_m)}</Text>
      ) : null}
    </Pressable>
  );
}

/**
 * Search any place in the Melbourne CBD by name, and pick it as an endpoint.
 *
 * The list underneath is whatever OpenStreetMap knows -- streets, laneways, tram stops,
 * the cafe -- not a fixed set of landmarks. Two things about it are worth keeping:
 *
 *   * EVERY RESULT IS ROUTABLE. The server drops matches that land further than 300 m
 *     from a walkable street, which is the same reach `/routes` allows. Nothing is
 *     offered here that the engine will then refuse, and when matches WERE dropped the
 *     count is shown, because "outside the CBD" and "no such place" are different
 *     answers and only one of them means try a different spelling.
 *   * THE EMPTY BOX IS NOT AN EMPTY SCREEN. It shows this device's own recent picks.
 *     There is no curated landmark list any more -- it was identical for everyone and
 *     went stale whenever a venue moved, and somewhere you actually went is the better
 *     suggestion. A first run genuinely has nothing to show, and says so.
 *   * CLOSED PLACES SAY SO. Live results carry OSM's `opening_hours` verdict, so a mall
 *     searched at 19:30 on a Sunday is marked Closed instead of looking like every other
 *     row. Recents carry no badge -- see lib/recents.ts for why.
 */
export function PlacePicker({
  visible,
  title,
  origin,
  onPick,
  onClose,
  onUseLocation,
}: {
  visible: boolean;
  title: string;
  /** What "how far" is measured from: the other end of the trip, or the GPS fix. Null
   * when neither is known yet, and then no distance is shown rather than a made-up one
   * from the middle of the CBD. */
  origin?: { lat: number; lon: number } | null;
  onPick: (p: Place) => void;
  onClose: () => void;
  onUseLocation?: () => void;
}) {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const input = useRef<TextInput>(null);
  const [query, setQuery] = useState('');
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recents, setRecents] = useState<Place[]>([]);

  // Each open starts clean. A stale query from the last time the sheet was open is a
  // worse default than the recents. Re-read the file on every open rather than once at
  // mount, so picking a place in the "from" sheet shows up in the "to" sheet.
  useEffect(() => {
    if (!visible) return;
    setQuery('');
    setRes(null);
    setError(null);
    let live = true;
    loadRecents().then((r) => live && setRecents(r));
    return () => {
      live = false;
    };
  }, [visible]);

  // Round-tripped through the query string anyway, and pinned to a primitive so the
  // effect below is not re-run by a new object with the same two numbers in it.
  const near = origin ? `${origin.lat},${origin.lon}` : null;

  useEffect(() => {
    if (!visible) return;
    const q = query.trim();
    const ac = new AbortController();
    // Debounced, and cancelled on the next keystroke -- so a fast typist leaves exactly
    // one request behind, and a slow network cannot deliver an old query's results over
    // a newer query's.
    const t = setTimeout(
      () => {
        setBusy(true);
        const [lat, lon] = (near ?? '').split(',').map(Number);
        api
          .search(q, near ? { lat, lon } : null, ac.signal)
          .then((r) => {
            setRes(r);
            setError(null);
            setBusy(false);
          })
          .catch((e) => {
            // An abort means a NEWER query already owns the spinner. Clearing it here
            // would blink it off between every two keystrokes.
            if (e.name === 'AbortError') return;
            setRes(null);
            setError(e.message);
            setBusy(false);
          });
      },
      q.length < MIN_QUERY ? 0 : DEBOUNCE_MS,
    );
    return () => {
      clearTimeout(t);
      ac.abort();
    };
  }, [query, visible, near]);

  const pick = useCallback(
    (p: Place) => {
      input.current?.blur();
      onPick(p);
    },
    [onPick],
  );

  const typed = query.trim().length >= MIN_QUERY;
  // An untyped box shows recents; anything typed shows what the server matched.
  const data = typed ? (res ? res.results : []) : recents;
  const nothing = !busy && !error && data.length === 0;

  return (
    <Modal
      visible={visible}
      animationType="slide"
      onRequestClose={onClose}
      // Android: same edge-to-edge geometry as iOS, so one set of insets is right on both.
      statusBarTranslucent
    >
      {/* NOT SafeAreaView. That component measures the native window it is inside, and
          RN's Modal is its own window on iOS -- one that reports no insets at all, so the
          title rendered straight through the notch and the clock. useSafeAreaInsets reads
          the SafeAreaProvider at the app root through React context, which the Modal does
          not interrupt, so these are the real insets. */}
      <View className="flex-1 bg-paper dark:bg-night" style={{ paddingTop: insets.top }}>
        <KeyboardAvoidingView
          className="flex-1"
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <View className="flex-row items-center justify-between px-4 py-3">
            <Text className="text-xl font-semibold text-ink dark:text-paper">{title}</Text>
            <Pressable onPress={onClose} hitSlop={12}>
              <Ionicons name="close" size={24} color={theme.inkMuted} />
            </Pressable>
          </View>

          <View className="mx-4 mb-2 flex-row items-center gap-2 rounded-xl bg-paper-sunken px-3 dark:bg-night-raised">
            <Ionicons name="search" size={18} color={theme.inkSoft} />
            <TextInput
              ref={input}
              value={query}
              onChangeText={setQuery}
              autoFocus
              autoCorrect={false}
              autoCapitalize="none"
              returnKeyType="search"
              placeholder="Search a street, station or place"
              placeholderTextColor={theme.inkSoft}
              className="flex-1 py-3 text-base"
              // Not a Tailwind class: NativeWind does not reliably reach TextInput's own
              // text colour, and an unstyled input is black-on-black in dark mode.
              style={{ color: theme.ink }}
            />
            {busy ? <ActivityIndicator size="small" color={theme.inkSoft} /> : null}
            {query.length > 0 && !busy ? (
              <Pressable onPress={() => setQuery('')} hitSlop={10}>
                <Ionicons name="close-circle" size={18} color={theme.inkSoft} />
              </Pressable>
            ) : null}
          </View>

          {onUseLocation ? (
            <Pressable
              onPress={onUseLocation}
              className="flex-row items-center gap-3 border-y border-line px-4 py-3.5 dark:border-line-dark"
            >
              <Ionicons name="locate" size={20} color={theme.indoor} />
              <Text className="text-base text-ink dark:text-paper">Use my location</Text>
            </Pressable>
          ) : null}

          <FlatList
            data={data}
            keyExtractor={keyOf}
            contentContainerStyle={{ paddingBottom: insets.bottom }}
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="on-drag"
            renderItem={({ item }) => (
              <Row place={item} onPress={() => pick(item)} color={theme.inkMuted} />
            )}
            ListHeaderComponent={
              !typed && data.length > 0 ? (
                <Text className="px-4 pb-1 pt-2 text-[13px] font-medium text-ink-soft">
                  Recent
                </Text>
              ) : null
            }
            ListFooterComponent={
              <View className="px-4 py-4">
                {error ? (
                  <Text className="text-[13px] leading-5 text-sun-fg dark:text-sun-dark">
                    {error}
                  </Text>
                ) : null}

                {/* Two different empty screens. A first run has no recents and needs to
                    be told what the box is for; a query that matched nothing needs to be
                    told that instead. */}
                {nothing && !error ? (
                  <Text className="text-[13px] leading-5 text-ink-soft">
                    {typed
                      ? `Nothing here matches “${query.trim()}”.`
                      : 'Search for a street, station or place. The ones you pick show up here next time.'}
                  </Text>
                ) : null}

                {/* The one thing a plain "no results" cannot say: it exists, and it is
                    not somewhere ShadeMe can walk you. Only shown when matches were
                    actually dropped for it. */}
                {res && res.outside > 0 ? (
                  <Text className="mt-1 text-[13px] leading-5 text-ink-soft">
                    {res.outside} {res.outside === 1 ? 'match is' : 'matches are'} outside
                    the area where ShadeMe covers (Melbourne CBD).
                  </Text>
                ) : null}

                {/* ODbL requires the attribution wherever the data is shown. */}
                {typed ? (
                  <Text className="mt-4 text-[11px] text-ink-soft">
                    Search data © OpenStreetMap contributors
                  </Text>
                ) : null}
              </View>
            }
          />
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}
