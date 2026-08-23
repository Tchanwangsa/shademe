import { Modal, Pressable, View, Text, FlatList } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Ionicons from '@expo/vector-icons/Ionicons';
import type { Place } from '../lib/api';
import { useTheme } from '../lib/theme';

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
  places: Place[];
  onPick: (p: Place) => void;
  onClose: () => void;
  onUseLocation?: () => void;
}) {
  const theme = useTheme();
  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView className="flex-1 bg-paper dark:bg-night">
        <View className="flex-row items-center justify-between px-4 py-3">
          <Text className="text-xl font-semibold text-ink dark:text-paper">{title}</Text>
          <Pressable onPress={onClose} hitSlop={12}>
            <Ionicons name="close" size={24} color={theme.inkMuted} />
          </Pressable>
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
          data={places}
          keyExtractor={(p) => p.name}
          renderItem={({ item }) => (
            <Pressable
              onPress={() => onPick(item)}
              className="border-b border-line px-4 py-3.5 dark:border-line-dark"
            >
              <Text className="text-base text-ink dark:text-paper">{item.name}</Text>
            </Pressable>
          )}
        />
      </SafeAreaView>
    </Modal>
  );
}
