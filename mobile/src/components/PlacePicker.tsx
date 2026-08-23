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
import { SafeAreaView } from 'react-native-safe-area-context';
import Ionicons from '@expo/vector-icons/Ionicons';

import { api, type Place, type SearchResponse } from '../lib/api';
import { placeIcon } from '../lib/format';
import { useTheme } from '../lib/theme';

/** Wait this long after the last keystroke before asking the server.
 *
 * NOT A PERFORMANCE TUNING. Nominatim's usage policy asks that an autocomplete send at
 * most one request per input rather than one per keystroke, and Photon asks for the same
 * restraint informally. 300 ms is below the pause between words for most typists, so the
 * search fires roughly once per thing typed -- which is what the policy is asking for.
 */
const DEBOUNCE_MS = 300;

/** Below this the server returns the curated list anyway, so there is nothing to send. */
const MIN_QUERY = 2;

/** A key that is stable across re-renders and unique per row. The name is neither: OSM
 * has six 7-Elevens in the CBD and they are all called 7-Eleven. */
const keyOf = (p: Place) => `${p.lat.toFixed(6)},${p.lon.toFixed(6)},${p.name}`;

function Row({ place, onPress, color }: { place: Place; onPress: () => void; color: string }) {
  return (
    <Pressable
      onPress={onPress}
      className="flex-row items-center gap-3 border-b border-line px-4 py-3 dark:border-line-dark active:bg-paper-sunken dark:active:bg-night"
    >
      <Ionicons name={placeIcon(place.kind) as any} size={20} color={color} />
      <View className="flex-1">
        <Text className="text-base text-ink dark:text-paper" numberOfLines={1}>
          {place.name}
        </Text>
        {place.address ? (
          <Text className="text-[13px] text-ink-soft" numberOfLines={1}>
            {place.address}
          </Text>
        ) : null}
      </View>
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
 *   * THE EMPTY BOX IS NOT AN EMPTY SCREEN. A query under two characters comes back as
 *     the curated landmarks, so opening the picker still offers somewhere to go.
 */
export function PlacePicker({
  visible,
  title,
  places,
  onPick,
  onClose,
  onUseLocation,
}: {
  visible: boolean;
  title: string;
  /** Fallback suggestions, shown before the first response lands. */
  places: Place[];
  onPick: (p: Place) => void;
  onClose: () => void;
  onUseLocation?: () => void;
}) {
  const theme = useTheme();
  const input = useRef<TextInput>(null);
  const [query, setQuery] = useState('');
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Each open starts clean. A stale query from the last time the sheet was open is a
  // worse default than the suggestions.
  useEffect(() => {
    if (!visible) return;
    setQuery('');
    setRes(null);
    setError(null);
  }, [visible]);

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
        api
          .search(q, ac.signal)
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
  }, [query, visible]);

  const pick = useCallback(
    (p: Place) => {
      input.current?.blur();
      onPick(p);
    },
    [onPick],
  );

  const typed = query.trim().length >= MIN_QUERY;
  // Before the first response, fall back to the list App already holds, so the picker
  // never opens blank on a cold start.
  const data = res ? res.results : typed ? [] : places;
  const nothing = !busy && !error && data.length === 0;

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView className="flex-1 bg-paper dark:bg-night">
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
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="on-drag"
            renderItem={({ item }) => (
              <Row place={item} onPress={() => pick(item)} color={theme.inkMuted} />
            )}
            ListHeaderComponent={
              !typed && data.length > 0 ? (
                <Text className="px-4 pb-1 pt-2 text-[13px] font-medium text-ink-soft">
                  Suggested
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

                {nothing && !error ? (
                  <Text className="text-[13px] leading-5 text-ink-soft">
                    Nothing here matches “{query.trim()}”.
                  </Text>
                ) : null}

                {/* The one thing a plain "no results" cannot say: it exists, and it is
                    not somewhere ShadeMe can walk you. Only shown when matches were
                    actually dropped for it. */}
                {res && res.outside > 0 ? (
                  <Text className="mt-1 text-[13px] leading-5 text-ink-soft">
                    {res.outside} {res.outside === 1 ? 'match is' : 'matches are'} outside
                    the Melbourne CBD, which is all ShadeMe covers.
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
      </SafeAreaView>
    </Modal>
  );
}
