"use client";

import { useEffect, useState } from "react";

function readIsPageVisible(): boolean {
  if (typeof document === "undefined") return true;
  return document.visibilityState !== "hidden";
}

export function usePageVisibility(): boolean {
  const [isPageVisible, setIsPageVisible] = useState(readIsPageVisible);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsPageVisible(readIsPageVisible());
    };

    handleVisibilityChange();
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  return isPageVisible;
}
