import {
  ConfidentialClientApplication,
  type ClientCredentialRequest,
  type Configuration,
} from "@azure/msal-node";

export class FnoTokenProvider {
  private readonly app: ConfidentialClientApplication;
  private readonly scope: string;

  constructor(options: {
    tenantId: string;
    clientId: string;
    clientSecret: string;
    baseUrl: string;
  }) {
    const config: Configuration = {
      auth: {
        clientId: options.clientId,
        authority: `https://login.microsoftonline.com/${options.tenantId}`,
        clientSecret: options.clientSecret,
      },
    };
    this.app = new ConfidentialClientApplication(config);
    this.scope = `${new URL(options.baseUrl).origin}/.default`;
  }

  async getAccessToken(): Promise<string> {
    const request: ClientCredentialRequest = {
      scopes: [this.scope],
    };
    const response = await this.app.acquireTokenByClientCredential(request);
    if (!response?.accessToken) {
      throw new Error("Microsoft Entra did not return an access token for the Finance & Operations resource.");
    }
    return response.accessToken;
  }
}
