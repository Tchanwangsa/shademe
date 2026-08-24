import './global.css';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, useWindowDimensions, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import BottomSheet, { BottomSheetScrollView } from '@gorhom/bottom-sheet';
import Ionicons from '@expo/vector-icons/Ionicons';
import * as Location from 'expo-location';

import { api, ApiError, type Conditions, type Place, type RoutesResponse } from './src/lib/api';
import { useTheme } from './src/lib/theme';
import { ConditionChips } from './src/components/ConditionChips';
import { OptionCard } from './src/components/OptionCard';
import { OptionDetail } from './src/components/OptionDetail';
import { PlacePicker } from './src/components/PlacePicker';
import { addRecent } from './src/lib/recents';
import { RouteMap } from './src/components/RouteMap';
import { HeatSensitivity } from './src/components/HeatSensitivity';
import { isDefault, loadWalker, NO_WALKER, saveWalker, type Walker } from './src/lib/walker';

/** Conditions are re-read on this cadence. The engine prices the wall clock, so the
 * chips have to keep up with it without the user doing anything. */
const CONDITIONS_MS = 5 * 60 * 1000;

/** Sheet detents, as fractions of window height. Kept as numbers rather than the
 * percentage strings BottomSheet wants, so the map can work out how much of itself is
 * actually visible; the strings are derived from these rather than maintained beside
 * them. */
const DETENTS = [0.22, 0.52, 0.9];

/** However far the sheet is pulled up, the map keeps this much of itself to fit a route
 * into. Without the cap, fitBounds at the 90% detent is asked to draw a route into a
 * sliver and zooms out until it is meaningless. */
const MAX_SHEET_FRACTION = 0.55;

function EndpointRow({
  icon,
  color,
  placeholder,
  place,
  onPress,
}: {
  icon: string;
  color: string;
  placeholder: string;
  place: Place | null;
  onPress: () => void;
}) {
  return (
    <Pressable onPress={onPress} className="flex-row items-center gap-2.5 py-2">
      <Ionicons name={icon as any} size={17} color={color} />
      <View className="flex-1">
        <Text
          className={place ? 'text-[15px] text-ink dark:text-paper' : 'text-[15px] text-ink-soft'}
          numberOfLines={1}
        >
          {place?.name ?? placeholder}
        </Text>
        {/* OSM has six 7-Elevens in the CBD, all called 7-Eleven. Without the street
            the row cannot say which one was picked. */}
        {place?.address ? (
          <Text className="text-[12px] text-ink-soft" numberOfLines={1}>
            {place.address}
          </Text>
        ) : null}
      </View>
    </Pressable>
  );
}

function ShadeMe() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const sheet = useRef<BottomSheet>(null);

  const [conditions, setConditions] = useState<Conditions | null>(null);
  const [from, setFrom] = useState<Place | null>(null);
  const [to, setTo] = useState<Place | null>(null);
  const [routes, setRoutes] = useState<RoutesResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [picking, setPicking] = useState<'from' | 'to' | null>(null);
  // The last GPS fix we are allowed to have. Only ever the fallback for "how far is
  // that?" in the picker -- routing still uses whatever the user actually picked.
  const [here, setHere] = useState<{ lat: number; lon: number } | null>(null);
  // The two heat-sensitivity answers. Read from the device once and then owned here;
  // `/routes` is re-asked whenever they change, because they change which walks the
  // engine searches for, not merely which one is highlighted.
  const [walker, setWalker] = useState<Walker>(NO_WALKER);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detent, setDetent] = useState(1);

  const { height: windowHeight } = useWindowDimensions();
  const snapPoints = useMemo(() => DETENTS.map((d) => `${d * 100}%`), []);
  // Derived from the detent, NOT measured from the scroll content: onLayout there
  // reports how tall the content is, which is unrelated to how much map the sheet
  // is covering once the content scrolls.
  const sheetHeight =
    windowHeight * Math.min(DETENTS[detent] ?? DETENTS[1], MAX_SHEET_FRACTION);

  // A last-known fix, asked for ONLY if permission has already been granted --
  // getForegroundPermissionsAsync reads the current state without prompting, so opening
  // the app never puts up a location dialog nobody asked for. It is cached by the OS, so
  // this costs nothing and it means the picker can say how far away things are before
  // either end of the trip has been chosen.
  useEffect(() => {
    let alive = true;
    (async () => {
      const { granted } = await Location.getForegroundPermissionsAsync();
      if (!granted || !alive) return;
      const pos = await Location.getLastKnownPositionAsync({});
      if (pos && alive) setHere({ lat: pos.coords.latitude, lon: pos.coords.longitude });
    })().catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    loadWalker()
      .then((w) => alive && setWalker(w))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      api
        .conditions()
        .then((c) => alive && setConditions(c))
        .catch(() => {});
    };
    tick();
    const id = setInterval(tick, CONDITIONS_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!from || !to) return;
    const ac = new AbortController();
    setLoading(true);
    setError(null);
    api
      .routes(from, to, walker, ac.signal)
      .then((r) => {
        setRoutes(r);
        setConditions(r.conditions);
        // The recommended option, not the first one. The list is sorted coolest-first,
        // and selecting its head was an unstated preference for shade at any cost --
        // see main.recommend. `recommended` is absent from a list of one, so the head
        // is still the fallback.
        setSelectedId(r.options.find((o) => o.recommended)?.id ?? r.options[0]?.id ?? null);
        sheet.current?.snapToIndex(1);
      })
      .catch((e) => {
        if (e.name === 'AbortError') return;
        setRoutes(null);
        setError(e.message);
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [from, to, walker]);

  const useMyLocation = useCallback(async () => {
    setPicking(null);
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') {
      setError('Location permission denied — search for a starting point instead.');
      return;
    }
    const pos = await Location.getCurrentPositionAsync({});
    const { latitude: lat, longitude: lon } = pos.coords;
    setHere({ lat, lon });
    // Name the fix from OpenStreetMap, but keep the user's own coordinates: routing from
    // the centroid of whatever building the fix landed in would move them without saying
    // so. A failed lookup is not a failed fix -- fall back to the plain label and let
    // /routes be the one to complain if the spot is unreachable.
    try {
      const here = await api.reverse(lat, lon);
      setFrom(here);
      if (!here.in_coverage) {
        setError(
          'You are outside the area ShadeMe covers — it only knows the Melbourne CBD. ' +
            'Search for a starting point inside it instead.',
        );
      }
    } catch {
      setFrom({ name: 'My location', lat, lon });
    }
  }, []);

  const options = routes?.options ?? [];
  const selected = options.find((o) => o.id === selectedId) ?? null;
  const personalised = !isDefault(walker);

  // Applied optimistically and written behind the render: the re-route fires off the
  // state change either way, and a switch that waits on a file write to move feels
  // broken. A failed write costs the setting next launch, nothing this session.
  const changeWalker = (w: Walker) => {
    setWalker(w);
    void saveWalker(w);
  };

  return (
    <View className="flex-1 bg-paper dark:bg-night">
      <RouteMap
        options={options}
        selected={selected}
        from={from}
        to={to}
        sheetHeight={sheetHeight}
      />

      <View
        className="absolute left-4 right-4"
        style={{ top: insets.top + 8 }}
        pointerEvents="box-none"
      >
        <ConditionChips conditions={conditions} />
      </View>

      <BottomSheet
        ref={sheet}
        index={1}
        snapPoints={snapPoints}
        onChange={setDetent}
        backgroundStyle={{ backgroundColor: theme.paper }}
        handleIndicatorStyle={{ backgroundColor: theme.line }}
      >
        <BottomSheetScrollView
          contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 24 }}
        >
          <Text className="mb-2 text-[22px] font-semibold text-ink dark:text-paper">
            Directions
          </Text>

          <View className="rounded-xl bg-paper-sunken px-3 py-1 dark:bg-night">
            <EndpointRow
              icon="radio-button-on"
              color={theme.indoor}
              placeholder="Choose a starting point"
              place={from}
              onPress={() => setPicking('from')}
            />
            <View className="h-px bg-line dark:bg-line-dark" />
            <EndpointRow
              icon="location"
              color="#D85A30"
              placeholder="Choose a destination"
              place={to}
              onPress={() => setPicking('to')}
            />
          </View>

          <HeatSensitivity
            walker={walker}
            onChange={changeWalker}
            detourCap={routes?.meta.detour_cap ?? null}
          />

          {loading ? (
            <View className="items-center py-8">
              <ActivityIndicator color={theme.shade} />
              <Text className="mt-2 text-[13px] text-ink-soft">Finding your options</Text>
            </View>
          ) : null}

          {error && !loading ? (
            <View className="mt-3 rounded-xl bg-sun-bg px-3 py-2.5 dark:bg-sun-fg/25">
              <Text className="text-[13px] leading-5 text-sun-fg dark:text-sun-dark">
                {error}
              </Text>
            </View>
          ) : null}

          {!loading && !error && options.length === 0 && from && to ? (
            <Text className="mt-4 text-[13px] text-ink-soft">No route between these two.</Text>
          ) : null}

          <View className="mt-3 gap-2">
            {options.map((o) => (
              <View key={o.id}>
                <OptionCard
                  option={o}
                  selected={o.id === selectedId}
                  personalised={personalised}
                  onPress={() => {
                    setSelectedId(o.id);
                    sheet.current?.snapToIndex(2);
                  }}
                />
                {o.id === selectedId && routes ? (
                  <View className="px-1 pt-3">
                    <OptionDetail option={o} meta={routes.meta} />
                  </View>
                ) : null}
              </View>
            ))}
          </View>
        </BottomSheetScrollView>
      </BottomSheet>

      <PlacePicker
        visible={picking !== null}
        title={picking === 'from' ? 'Starting point' : 'Destination'}
        // Measured from the OTHER end of the trip when there is one -- picking a
        // destination, "how far" means how far from where the walk starts -- and from the
        // phone otherwise. Null before either exists, and then no distances are shown.
        origin={(picking === 'from' ? to : from) ?? here}
        onPick={(p) => {
          if (picking === 'from') setFrom(p);
          else setTo(p);
          setPicking(null);
          // Fire and forget: the pick has already been applied, and a device that cannot
          // write its recents file should still be able to plan a walk.
          void addRecent(p);
        }}
        onClose={() => setPicking(null)}
        onUseLocation={picking === 'from' ? useMyLocation : undefined}
      />

      <StatusBar style={theme.isDark ? 'light' : 'dark'} />
    </View>
  );
}

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ShadeMe />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
