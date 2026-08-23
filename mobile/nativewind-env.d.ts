/// <reference types="nativewind/types" />

// TS 6 rejects a side-effect import of a stylesheet without this. Metro resolves
// ./global.css through nativewind/metro; TypeScript only needs to be told it exists.
declare module '*.css';
