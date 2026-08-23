import './global.css';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';
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
import { RouteMap } from './src/components/RouteMap';

/** Conditions are re-read on this cadence. The engine prices the wall clock, so the
 * chips have to keep up with it without the user doing anything. */
const CONDITIONS_MS = 5 * 60 * 1000;

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
      <Text
        className={
          place
            ? 'flex-1 text-[15px] text-ink dark:text-paper'
            : 'flex-1 text-[15px] text-ink-soft'
        }
        numberOfLines={1}
      >
        {place?.name ?? placeholder}
      </Text>
    </Pressable>
  );
}

function Laneway() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const sheet = useRef<BottomSheet>(null);

  const [places, setPlaces] = useState<Place[]>([]);
  const [conditions, setConditions] = useState<Conditions | null>(null);
  const [from, setFrom] = useState<Place | null>(null);
  const [to, setTo] = useState<Place | null>(null);
  const [routes, setRoutes] = useState<RoutesResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [picking, setPicking] = useState<'from' | 'to' | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sheetHeight, setSheetHeight] = useState(320);

  const snapPoints = useMemo(() => ['22%', '52%', '90%'], []);

  useEffect(() => {
    const ac = new AbortController();
    api.places(ac.signal).then(setPlaces).catch((e: ApiError) => setError(e.message));
    return () => ac.abort();
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
      .routes(from, to, ac.signal)
      .then((r) => {
        setRoutes(r);
        setConditions(r.conditions);
        setSelectedId(r.options[0]?.id ?? null);
        sheet.current?.snapToIndex(1);
      })
      .catch((e) => {
        if (e.name === 'AbortError') return;
        setRoutes(null);
        setError(e.message);
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [from, to]);

  const useMyLocation = useCallback(async () => {
    setPicking(null);
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') {
      setError('Location permission denied — pick a starting point from the list instead.');
      return;
    }
    const pos = await Location.getCurrentPositionAsync({});
    setFrom({ name: 'My location', lat: pos.coords.latitude, lon: pos.coords.longitude });
  }, []);

  const options = routes?.options ?? [];
  const selected = options.find((o) => o.id === selectedId) ?? null;

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
        onChange={() => {}}
        backgroundStyle={{ backgroundColor: theme.paper }}
        handleIndicatorStyle={{ backgroundColor: theme.line }}
      >
        <BottomSheetScrollView
          contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 24 }}
          onLayout={(e) => setSheetHeight(e.nativeEvent.layout.height)}
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

          {loading ? (
            <View className="items-center py-8">
              <ActivityIndicator color={theme.shade} />
              <Text className="mt-2 text-[13px] text-ink-soft">
                Pricing the walk at {conditions ? `${conditions.hour}:00` : 'now'}
              </Text>
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

          {routes && options.length === 1 ? (
            <Text className="mt-3 text-[12px] leading-5 text-ink-soft">
              One option: every thermal preference the engine tried came back with the same
              walk.
            </Text>
          ) : null}
        </BottomSheetScrollView>
      </BottomSheet>

      <PlacePicker
        visible={picking !== null}
        title={picking === 'from' ? 'Starting point' : 'Destination'}
        places={places}
        onPick={(p) => {
          if (picking === 'from') setFrom(p);
          else setTo(p);
          setPicking(null);
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
        <Laneway />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
