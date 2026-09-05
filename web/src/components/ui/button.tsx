import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

const buttonVariants = cva("button", { variants: { variant: { secondary: "secondary", primary: "primary" } }, defaultVariants: { variant: "secondary" } });
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants> & { asChild?: boolean };
export function Button({ asChild, className, variant, ...props }: ButtonProps) { const Component = asChild ? Slot : "button"; return <Component className={cn(buttonVariants({ variant }), className)} {...props} />; }
