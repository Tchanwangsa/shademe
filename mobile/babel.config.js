module.exports = function (api) {
  api.cache(true);
  return {
    presets: [
      ['babel-preset-expo', { jsxImportSource: 'nativewind' }],
      'nativewind/babel',
    ],
    // Reanimated 4 moved its Babel plugin into react-native-worklets. This must stay
    // last in the plugin list.
    plugins: ['react-native-worklets/plugin'],
  };
};
