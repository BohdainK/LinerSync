import type { UseMutationResult } from "@tanstack/react-query";
import type { ConnectionOut, ConnectionTestOut, SetupStep } from "@/api/types";
import { BrowserSignInStep } from "./steps/BrowserSignInStep";
import { DeviceCodeStep } from "./steps/DeviceCodeStep";
import { FormStep } from "./steps/FormStep";
import { InfoStep } from "./steps/InfoStep";
import { OAuthStep } from "./steps/OAuthStep";
import { TestStep } from "./steps/TestStep";

/** The body of one setup step, by kind. */
export function StepContent({
  service,
  name,
  step,
  conn,
  signInError,
  test,
  onAcknowledge,
  onSaved,
  onSignedIn,
}: {
  service: string;
  name: string;
  step: SetupStep;
  conn: ConnectionOut;
  signInError: string | null;
  test: UseMutationResult<ConnectionTestOut, Error, void>;
  onAcknowledge: () => void;
  onSaved: () => void;
  onSignedIn: () => void;
}) {
  switch (step.kind) {
    case "info":
      return <InfoStep service={service} step={step} onAcknowledge={onAcknowledge} />;
    case "form":
      return <FormStep service={service} step={step} conn={conn} onSaved={onSaved} />;
    case "oauth":
      return <OAuthStep service={service} name={name} step={step} signInError={signInError} />;
    case "browser_sign_in":
      return <BrowserSignInStep service={service} name={name} step={step} onSignedIn={onSignedIn} />;
    case "device_code":
      return <DeviceCodeStep service={service} name={name} step={step} onSignedIn={onSignedIn} />;
    case "test":
      return (
        <TestStep
          service={service}
          step={step}
          onTest={() => test.mutate()}
          pending={test.isPending}
          result={test.data}
          error={test.error}
        />
      );
  }
}
